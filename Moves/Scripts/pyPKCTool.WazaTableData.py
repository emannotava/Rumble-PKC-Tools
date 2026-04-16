#!/usr/bin/env python3
import argparse
import json
import os
import re
import struct
import sys
from enum import IntEnum
from typing import Dict, List, Optional, Tuple

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
	sys.path.insert(0, SCRIPT_DIR)

try:
	from script_functions import script_functions
	from pyPKCEnumLib import MoveEnum, SpeciesEnum
except ImportError as exc:
	print(f"Failed to import support modules: {exc}", file=sys.stderr)
	sys.exit(1)

FORMAT_VERSION = "wazatable-edit-json-1"
FALLBACK_MOVE_ENUM_MAX_ID = 547
FALLBACK_SPECIES_ENUM_MAX_ID = 600
SECTION_KIND_BY_ID = {
	-6: "level_move_pairs",
	-5: "move_ids",
	-4: "move_ids",
	-3: "move_ids",
	-2: "move_ids",
	-1: "move_ids",
}
SECTION_KEY_BY_ID = {
	-6: "level_up_moves",
	-5: "tutor_moves",
	-4: "egg_moves",
	-3: "tm_moves",
	-2: "hm_moves",
	-1: "special_tutor_moves",
}
SECTION_ID_BY_KEY = {value: key for key, value in SECTION_KEY_BY_ID.items()}
CANONICAL_SECTION_ORDER = [
	"hm_moves",
	"tutor_moves",
	"level_up_moves",
	"special_tutor_moves",
	"egg_moves",
	"tm_moves",
]
SECTION_MAX_COUNTS = {
	"hm_moves": 7,
	"tutor_moves": 32,
	"level_up_moves": 20,
	"special_tutor_moves": 1,
	"egg_moves": 13,
	"tm_moves": 67,
}
BASE_SETTER_NAME = "method_pprSetWazaLevelForAllFormno"
FORM_SETTER_NAME = "method_pprSetWazaLevel"
VALID_SETTER_NAMES = {BASE_SETTER_NAME, FORM_SETTER_NAME}
VARARGS_SENTINEL = 0xFFFFFFFF

MOVE_ENUM_RE = re.compile(r"^MOVE_(\d{4,})$")
SPECIES_ENUM_RE = re.compile(r"^SPECIES_(\d{4,})$")

MOVE_ID_TO_SYMBOL = {int(member): member.name for member in MoveEnum}
MOVE_SYMBOL_TO_ID = {member.name: int(member) for member in MoveEnum}
SPECIES_ID_TO_SYMBOL = {int(member): member.name for member in SpeciesEnum}
SPECIES_SYMBOL_TO_ID = {member.name: int(member) for member in SpeciesEnum}


class PKCOpCode(IntEnum):
	nop_0 = 0x00
	nop_1 = 0x02
	negate = 0x03
	complement = 0x04
	add = 0x05
	sub = 0x06
	multiply = 0x07
	divide = 0x08
	store = 0x0A
	cmp_eq = 0x0B
	cmp_neq = 0x0C
	cmp_lt = 0x0D
	cmp_geq = 0x0E
	cmp_leq = 0x0F
	cmp_gt = 0x10
	is_zero = 0x11
	bitwise_and = 0x14
	bitwise_xor = 0x15
	bitwise_or = 0x16
	bitwise_rshift = 0x17
	bitwise_lshift = 0x18
	access = 0x19
	call_imm = 0x1A
	call_ext_0 = 0x1B
	jmp_imm = 0x1C
	jmp_if_false = 0x1D
	function_epilogue = 0x1E
	pop = 0x1F
	function_prologue = 0x20
	store_ptr = 0x22
	exit = 0x23
	jmp_if_true = 0x24
	jmp_if_false_c = 0x25
	jmp_if_true_c = 0x26
	call_ext_1 = 0x28
	peek_nop = 0x36
	access_ptr = 0x3C
	push_int = 0x40
	push_s_f = 0x41
	push_str = 0x42
	push_dat = 0x43
	nop_2 = 0x4A
	switch = 0x4C


FUNCTION_INDEX_BY_NAME: Dict[str, int] = {}
for function_index, pair in enumerate(script_functions):
	if not isinstance(pair, tuple) or len(pair) != 2:
		continue
	function_name, _declared_argc = pair
	if function_name and function_name not in FUNCTION_INDEX_BY_NAME:
		FUNCTION_INDEX_BY_NAME[function_name] = function_index


class StackValue:
	def __init__(self, kind: str, value, source_instruction_index: Optional[int]):
		self.kind = kind
		self.value = value
		self.source_instruction_index = source_instruction_index


class SimpleStack:
	def __init__(self):
		self.items: List[StackValue] = []

	def push(self, item: StackValue):
		self.items.append(item)

	def popn(self, count: int) -> List[StackValue]:
		if count <= 0:
			return []
		if len(self.items) < count:
			missing = count - len(self.items)
			result = [StackValue("unknown", "<?>", None) for _ in range(missing)] + self.items[:]
			self.items.clear()
			return result
		result = self.items[-count:]
		del self.items[-count:]
		return result


class BlockRecord:
	def __init__(
		self,
		block_index: int,
		key_values: List[int],
		setter_name: str,
		setter_arg_values: List[int],
		push_instruction_indices: List[int],
		get_call_instruction_index: int,
		getter_nop_instruction_index: int,
		setter_call_instruction_index: int,
		setter_nop_instruction_index: int,
		pop_instruction_index: int,
		instructions: List[dict],
	):
		self.block_index = block_index
		self.key_values = key_values
		self.setter_name = setter_name
		self.setter_arg_values = setter_arg_values
		self.push_instruction_indices = push_instruction_indices
		self.get_call_instruction_index = get_call_instruction_index
		self.getter_nop_instruction_index = getter_nop_instruction_index
		self.setter_call_instruction_index = setter_call_instruction_index
		self.setter_nop_instruction_index = setter_nop_instruction_index
		self.pop_instruction_index = pop_instruction_index
		self.start_instruction_index = push_instruction_indices[0]
		self.end_instruction_index = pop_instruction_index
		start_instruction = instructions[self.start_instruction_index]
		end_instruction = instructions[self.end_instruction_index]
		self.start_byte_offset = start_instruction["byte_offset"]
		self.end_byte_offset = end_instruction["byte_offset"] + end_instruction["length_bytes"]
		self.start_pc_units = start_instruction["pc_units"]
		self.end_pc_units = end_instruction["pc_units"]
		self.get_call_pc_units = instructions[self.get_call_instruction_index]["pc_units"]
		self.setter_call_pc_units = instructions[self.setter_call_instruction_index]["pc_units"]
		self.species = key_values[0]
		self.form = key_values[1] if len(key_values) > 1 else None
		self.key = stringify_species_form(self.species, self.form)
		self.learnsets = parse_learnset_sections(setter_arg_values)


def opcode_name(opcode: int) -> str:
	try:
		return PKCOpCode(opcode).name
	except ValueError:
		return f"opcode_0x{opcode:02X}"


def sign24(value: int) -> int:
	return value - 0x1000000 if value & 0x800000 else value


def hex_bytes(data: bytes) -> str:
	return data.hex().upper()


def decode_ascii_cstring_pool(raw: bytes, expected_count: Optional[int]):
	if not raw:
		return []
	parts = raw.split(b"\x00")
	if parts[-1] != b"":
		return None
	entries = []
	for part in parts[:-1]:
		try:
			entries.append(part.decode("ascii"))
		except UnicodeDecodeError:
			return None
	if expected_count is not None and expected_count != len(entries):
		return None
	return entries


def decode_utf16be_cstring_pool(raw: bytes, expected_count: Optional[int]):
	if not raw:
		return []
	if len(raw) % 2 != 0:
		return None
	entries = []
	current = bytearray()
	for index in range(0, len(raw), 2):
		word = raw[index:index + 2]
		if word == b"\x00\x00":
			try:
				entries.append(current.decode("utf-16-be"))
			except UnicodeDecodeError:
				return None
			current = bytearray()
		else:
			current.extend(word)
	if current:
		try:
			entries.append(current.decode("utf-16-be"))
		except UnicodeDecodeError:
			return None
	if expected_count is not None and expected_count != len(entries):
		return None
	return entries


def decode_data_section(raw: bytes, expected_count: Optional[int]):
	if not raw:
		return {
			"encoding": "raw",
			"entries": [],
			"raw_hex": "",
			"count": expected_count or 0,
		}

	utf16_entries = decode_utf16be_cstring_pool(raw, expected_count)
	if utf16_entries is not None:
		return {
			"encoding": "utf16be_cstring_pool",
			"entries": utf16_entries,
			"raw_hex": hex_bytes(raw),
			"count": len(utf16_entries),
		}

	return {
		"encoding": "raw",
		"entries": [],
		"raw_hex": hex_bytes(raw),
		"count": expected_count or 0,
	}


def read_pkc(path: str):
	with open(path, "rb") as infile:
		raw = infile.read()

	if len(raw) < 0x20:
		raise ValueError("File is too small to be a PKC")

	magic = raw[0:4]
	if magic not in (b"pk\x1A\x20", b"\x20\x1Akp", b"PK\x1A\x20", b"\x20\x1AKP"):
		raise ValueError(f"Unsupported PKC magic: {magic!r}")

	flags, code_size, data_size, data_count, string_size, string_count, reserved = struct.unpack(">7I", raw[4:0x20])
	code_start = 0x20
	data_start = code_start + code_size
	string_start = data_start + data_size
	trailing_start = string_start + string_size

	if trailing_start > len(raw):
		raise ValueError("Section sizes run past end of file")

	return {
		"path": path,
		"basename": os.path.basename(path),
		"magic": magic,
		"magic_hex": hex_bytes(magic),
		"flags": flags,
		"code_size": code_size,
		"data_size": data_size,
		"data_count": data_count,
		"string_size": string_size,
		"string_count": string_count,
		"reserved": reserved,
		"raw": raw,
		"code_raw": raw[code_start:data_start],
		"data_raw": raw[data_start:string_start],
		"string_raw": raw[string_start:trailing_start],
		"trailing_raw": raw[trailing_start:],
	}


def decode_instruction_stream(code_raw: bytes, string_entries: List[str], data_section: dict):
	instructions = []
	byte_offset = 0
	pc_units = 0
	data_entries = data_section.get("entries", [])

	while byte_offset < len(code_raw):
		if byte_offset + 4 > len(code_raw):
			raise ValueError(f"Truncated instruction header at byte offset 0x{byte_offset:X}")

		start = byte_offset
		opcode = code_raw[byte_offset]
		imm = int.from_bytes(code_raw[byte_offset + 1:byte_offset + 4], "big")
		byte_offset += 4
		payload = b""
		length_units = 1

		if opcode == PKCOpCode.push_s_f.value:
			if byte_offset + 4 > len(code_raw):
				raise ValueError(f"Truncated push_s_f payload at byte offset 0x{byte_offset:X}")
			payload = code_raw[byte_offset:byte_offset + 4]
			byte_offset += 4
			length_units = 2
		elif opcode == PKCOpCode.function_prologue.value:
			if byte_offset + 4 > len(code_raw):
				raise ValueError(f"Truncated function_prologue payload at byte offset 0x{byte_offset:X}")
			payload = code_raw[byte_offset:byte_offset + 4]
			byte_offset += 4
			length_units = 2
		elif opcode == PKCOpCode.switch.value:
			case_count = imm >> 16
			payload_size = case_count * 2
			if case_count & 1:
				payload_size += 2
			if byte_offset + payload_size > len(code_raw):
				raise ValueError(f"Truncated switch payload at byte offset 0x{byte_offset:X}")
			payload = code_raw[byte_offset:byte_offset + payload_size]
			byte_offset += payload_size

		instruction = {
			"index": len(instructions),
			"pc_units": pc_units,
			"byte_offset": start,
			"opcode": opcode,
			"opname": opcode_name(opcode),
			"imm": imm,
			"length_bytes": byte_offset - start,
			"length_units": length_units,
			"payload_hex": hex_bytes(payload),
		}

		if opcode == PKCOpCode.push_s_f.value:
			instruction["decoded"] = {
				"type": "float",
				"value": struct.unpack(">f", payload)[0],
			}
		elif opcode == PKCOpCode.function_prologue.value:
			payload_value = struct.unpack(">I", payload)[0]
			instruction["decoded"] = {
				"type": "function_prologue_payload",
				"value_u32": payload_value,
			}
		elif opcode == PKCOpCode.switch.value:
			case_count = imm >> 16
			offsets = []
			for pos in range(0, case_count * 2, 2):
				offsets.append(struct.unpack(">h", payload[pos:pos + 2])[0])
			instruction["decoded"] = {
				"type": "switch",
				"case_count": case_count,
				"case_offsets_s16": offsets,
			}
		elif opcode in (PKCOpCode.store.value, PKCOpCode.store_ptr.value, PKCOpCode.access.value, PKCOpCode.access_ptr.value):
			instruction["decoded"] = {
				"type": "frame_access",
				"segment": imm >> 16,
				"slot": imm & 0xFFFF,
			}
		elif opcode == PKCOpCode.push_int.value:
			instruction["decoded"] = {
				"type": "int24",
				"value_signed": sign24(imm),
				"value_unsigned": imm,
			}
		elif opcode == PKCOpCode.push_str.value:
			value = string_entries[imm] if 0 <= imm < len(string_entries) else None
			instruction["decoded"] = {
				"type": "string_index",
				"index": imm,
				"value": value,
			}
		elif opcode == PKCOpCode.push_dat.value:
			value = data_entries[imm] if 0 <= imm < len(data_entries) else None
			instruction["decoded"] = {
				"type": "data_index",
				"index": imm,
				"value": value,
			}
		elif opcode in (PKCOpCode.call_ext_0.value, PKCOpCode.call_ext_1.value):
			if 0 <= imm < len(script_functions):
				func_name, argc_declared = script_functions[imm]
			else:
				func_name, argc_declared = None, None
			instruction["decoded"] = {
				"type": "external_call",
				"function_index": imm,
				"function_name": func_name,
				"declared_argc": argc_declared,
			}
		elif opcode in (
			PKCOpCode.call_imm.value,
			PKCOpCode.jmp_imm.value,
			PKCOpCode.jmp_if_false.value,
			PKCOpCode.jmp_if_true.value,
			PKCOpCode.jmp_if_false_c.value,
			PKCOpCode.jmp_if_true_c.value,
		):
			instruction["decoded"] = {
				"type": "pc_immediate",
				"value": imm,
			}

		instructions.append(instruction)
		pc_units += length_units

	for index, instruction in enumerate(instructions):
		if instruction["opcode"] == PKCOpCode.call_ext_1.value:
			next_instruction = instructions[index + 1] if index + 1 < len(instructions) else None
			if next_instruction and next_instruction["opcode"] == PKCOpCode.nop_0.value:
				instruction.setdefault("decoded", {})
				instruction["decoded"]["resolved_argc_from_next_nop_0"] = next_instruction["imm"]

	return instructions


def load_move_enum_map() -> Dict[int, str]:
	mapping = {item_id: f"MOVE_{item_id:04d}" for item_id in range(1, FALLBACK_MOVE_ENUM_MAX_ID + 1)}
	mapping.update(MOVE_ID_TO_SYMBOL)
	return mapping



def load_species_enum_map() -> Dict[int, str]:
	mapping = {item_id: f"SPECIES_{item_id:04d}" for item_id in range(0, FALLBACK_SPECIES_ENUM_MAX_ID + 1)}
	mapping.update(SPECIES_ID_TO_SYMBOL)
	return mapping



def invert_enum_map(mapping: Dict[int, str]) -> Dict[str, int]:
	return {symbol: item_id for item_id, symbol in mapping.items() if isinstance(symbol, str)}


def parse_fallback_enum_symbol(value, pattern: re.Pattern) -> Optional[int]:
	if not isinstance(value, str):
		return None
	match = pattern.fullmatch(value)
	if match is None:
		return None
	return int(match.group(1))


def species_id_to_symbol(species: int, species_enum_map: Dict[int, str]) -> str:
	return species_enum_map.get(species, f"SPECIES_{species:04d}")


def move_id_to_symbol(move_id: int, move_enum_map: Dict[int, str]) -> str:
	return move_enum_map.get(move_id, f"MOVE_{move_id:04d}")


def symbol_or_int_to_species_id(value, species_symbol_to_id: Dict[str, int]) -> int:
	if isinstance(value, int):
		return value
	if isinstance(value, str):
		species = species_symbol_to_id.get(value)
		if species is not None:
			return species
		species = parse_fallback_enum_symbol(value, SPECIES_ENUM_RE)
		if species is not None:
			return species
	raise ValueError(f"Invalid species value: {value!r}")


def symbol_or_int_to_move_id(value, move_symbol_to_id: Dict[str, int]) -> int:
	if isinstance(value, int):
		return value
	if isinstance(value, str):
		move_id = move_symbol_to_id.get(value)
		if move_id is not None:
			return move_id
		move_id = parse_fallback_enum_symbol(value, MOVE_ENUM_RE)
		if move_id is not None:
			return move_id
	raise ValueError(f"Invalid move value: {value!r}")


def stringify_species_form(species: int, form: Optional[int]) -> str:
	species_enum_map = load_species_enum_map()
	species_symbol = species_id_to_symbol(species, species_enum_map)
	if form is None:
		return species_symbol
	return f"{species_symbol}, {form}"


def normalize_species_form(entry: dict, species_symbol_to_id: Dict[str, int]) -> Tuple[int, Optional[int]]:
	species = symbol_or_int_to_species_id(entry.get("species"), species_symbol_to_id)
	form = entry.get("form")
	if form is not None and not isinstance(form, int):
		raise ValueError(f"Entry block_index={entry.get('block_index')} has invalid form")
	return species, form


def parse_learnset_sections(raw_args: List[int]) -> List[dict]:
	sections: List[dict] = []
	current_section = None
	for value in raw_args:
		if not isinstance(value, int):
			raise ValueError(f"Unexpected non-integer learnset value: {value!r}")
		if value < 0:
			if current_section is not None:
				sections.append(finalize_section(current_section))
			current_section = {
				"section_id": value,
				"raw_values": [],
			}
			continue
		if current_section is None:
			raise ValueError("Learnset values encountered before section marker")
		current_section["raw_values"].append(value)
	if current_section is not None:
		sections.append(finalize_section(current_section))
	return sections


def finalize_section(section: dict) -> dict:
	section_id = section["section_id"]
	raw_values = section["raw_values"]
	section_kind = SECTION_KIND_BY_ID.get(section_id, "move_ids")
	section_key = SECTION_KEY_BY_ID.get(section_id, f"section_{section_id}_moves")
	result = {
		"section_id": section_id,
		"section_key": section_key,
		"storage": section_kind,
	}
	if section_kind == "level_move_pairs":
		if len(raw_values) % 2 != 0:
			raise ValueError(f"Level-up section {section_id} has an odd number of values: {raw_values!r}")
		entries = []
		for index in range(0, len(raw_values), 2):
			entries.append({
				"level": raw_values[index],
				"move_id": raw_values[index + 1],
			})
		result["entries"] = entries
	else:
		result["move_ids"] = list(raw_values)
	return result


def export_learnsets_with_enums(learnsets: List[dict], move_enum_map: Dict[int, str]) -> dict:
	exported = {}
	for section in learnsets:
		section_key = section["section_key"]
		if section["storage"] == "level_move_pairs":
			exported_entries = []
			for pair in section["entries"]:
				exported_entries.append({
					"level": pair["level"],
					"move": move_id_to_symbol(pair["move_id"], move_enum_map),
				})
			exported[section_key] = exported_entries
		else:
			exported[section_key] = [move_id_to_symbol(move_id, move_enum_map) for move_id in section["move_ids"]]
	return exported


def iter_ordered_learnset_items(learnsets) -> List[tuple]:
	if isinstance(learnsets, dict):
		seen = set()
		items = []
		for section_key in CANONICAL_SECTION_ORDER:
			if section_key in learnsets:
				items.append((section_key, learnsets[section_key]))
				seen.add(section_key)
		for section_key, payload in learnsets.items():
			if section_key not in seen:
				items.append((section_key, payload))
		return items
	if isinstance(learnsets, list):
		items = []
		for section in learnsets:
			if not isinstance(section, dict):
				raise ValueError("Each learnset section must be an object")
			section_key = section.get("section_key")
			section_id = section.get("section_id")
			if not isinstance(section_key, str):
				if isinstance(section_id, int):
					section_key = SECTION_KEY_BY_ID.get(section_id, f"section_{section_id}_moves")
				else:
					raise ValueError(f"Invalid legacy learnset section: {section!r}")
			items.append((section_key, section))
		return items
	raise ValueError("learnsets must be an object")


def validate_learnsets(learnsets, move_symbol_to_id: Dict[str, int], entry_label: str, unsafe: bool = False):
	items = iter_ordered_learnset_items(learnsets)
	for section_key, payload in items:
		if section_key == "level_up_moves":
			entries = payload if not isinstance(payload, dict) else payload.get("entries")
			if not isinstance(entries, list):
				raise ValueError(f"{entry_label}: section {section_key!r} must be a list of level/move objects")
			levels = []
			moves = []
			for pair in entries:
				if not isinstance(pair, dict):
					raise ValueError(f"{entry_label}: section {section_key!r} entry must be an object")
				level = pair.get("level")
				move_value = pair.get("move", pair.get("move_id"))
				if not isinstance(level, int):
					raise ValueError(f"{entry_label}: section {section_key!r} entry has invalid level")
				if level < 0 or level > 100:
					raise ValueError(f"{entry_label}: level_up move level {level} is outside the observed 0-100 range")
				move_id = symbol_or_int_to_move_id(move_value, move_symbol_to_id)
				levels.append(level)
				moves.append(move_id)
			if len(entries) > SECTION_MAX_COUNTS[section_key] and not unsafe:
				raise ValueError(f"{entry_label}: section {section_key!r} has {len(entries)} entries, above the vanilla maximum of {SECTION_MAX_COUNTS[section_key]}. This is likely to crash in-game. Re-run with --unsafe to bypass.")
			if len(moves) != len(set(moves)) and not unsafe:
				raise ValueError(f"{entry_label}: section {section_key!r} contains duplicate moves. Re-run with --unsafe to bypass.")
			if levels != sorted(levels) and not unsafe:
				raise ValueError(f"{entry_label}: section {section_key!r} is not sorted by ascending level. Re-run with --unsafe to bypass.")
		else:
			moves = payload if not isinstance(payload, dict) else payload.get("moves", payload.get("move_ids"))
			if not isinstance(moves, list):
				raise ValueError(f"{entry_label}: section {section_key!r} must be a list of moves")
			resolved_moves = [symbol_or_int_to_move_id(move_value, move_symbol_to_id) for move_value in moves]
			max_count = SECTION_MAX_COUNTS.get(section_key)
			if max_count is not None and len(moves) > max_count and not unsafe:
				raise ValueError(f"{entry_label}: section {section_key!r} has {len(moves)} entries, above the vanilla maximum of {max_count}. This is likely to crash in-game. Re-run with --unsafe to bypass.")
			if len(resolved_moves) != len(set(resolved_moves)) and not unsafe:
				raise ValueError(f"{entry_label}: section {section_key!r} contains duplicate moves. Re-run with --unsafe to bypass.")


def flatten_learnset_sections(learnsets, move_symbol_to_id: Dict[str, int]) -> List[int]:
	items = iter_ordered_learnset_items(learnsets)

	raw_args: List[int] = []
	for section_key, payload in items:
		section_id = SECTION_ID_BY_KEY.get(section_key)
		if section_id is None:
			match = re.fullmatch(r"section_(-?\d+)_moves", section_key)
			if not match:
				raise ValueError(f"Unknown learnset section key: {section_key!r}")
			section_id = int(match.group(1))
		if section_id >= 0:
			raise ValueError(f"Invalid section_id for key {section_key!r}: {section_id!r}")
		expected_storage = SECTION_KIND_BY_ID.get(section_id, "move_ids")
		raw_args.append(section_id)
		if expected_storage == "level_move_pairs":
			entries = payload
			if isinstance(payload, dict):
				entries = payload.get("entries")
			if not isinstance(entries, list):
				raise ValueError(f"Section {section_key!r} must be a list of level/move objects")
			for pair in entries:
				if not isinstance(pair, dict):
					raise ValueError(f"Section {section_key!r} entry must be an object")
				level = pair.get("level")
				move_value = pair.get("move", pair.get("move_id"))
				move_id = symbol_or_int_to_move_id(move_value, move_symbol_to_id)
				if not isinstance(level, int):
					raise ValueError(f"Section {section_key!r} entry has invalid level")
				raw_args.extend([level, move_id])
		else:
			moves = payload
			if isinstance(payload, dict):
				moves = payload.get("moves", payload.get("move_ids"))
			if not isinstance(moves, list):
				raise ValueError(f"Section {section_key!r} must be a list of moves")
			for move_value in moves:
				raw_args.append(symbol_or_int_to_move_id(move_value, move_symbol_to_id))
	return raw_args


def encode_instruction(opcode: int, imm: int, payload: bytes = b"") -> bytes:
	if not (0 <= opcode <= 0xFF):
		raise ValueError(f"Opcode out of range: {opcode}")
	if not (0 <= imm <= 0xFFFFFF):
		raise ValueError(f"Immediate out of range for 24-bit field: {imm}")
	return bytes([opcode]) + imm.to_bytes(3, "big") + payload


def encode_push_int(value: int) -> bytes:
	if not (-0x800000 <= value <= 0x7FFFFF):
		raise ValueError(f"Integer out of signed 24-bit range: {value}")
	imm = value & 0xFFFFFF
	return encode_instruction(PKCOpCode.push_int.value, imm)


def encode_call_ext_1(function_name: str, argc: int) -> bytes:
	function_index = FUNCTION_INDEX_BY_NAME.get(function_name)
	if function_index is None:
		raise ValueError(f"Function not found in script_functions.py: {function_name}")
	return encode_instruction(PKCOpCode.call_ext_1.value, function_index) + encode_instruction(PKCOpCode.nop_0.value, argc)


def encode_pop() -> bytes:
	return encode_instruction(PKCOpCode.pop.value, 0)


def build_block_bytes(species: int, form: Optional[int], setter_name: str, learnsets, move_symbol_to_id: Dict[str, int], unsafe: bool = False) -> bytes:
	if setter_name not in VALID_SETTER_NAMES:
		raise ValueError(f"Unsupported setter name: {setter_name}")
	if form is None and setter_name != BASE_SETTER_NAME:
		raise ValueError("form=null must use method_pprSetWazaLevelForAllFormno")
	if form is not None and setter_name != FORM_SETTER_NAME:
		raise ValueError("form-specific entries must use method_pprSetWazaLevel")

	entry_label = stringify_species_form(species, form)
	validate_learnsets(learnsets, move_symbol_to_id, entry_label, unsafe=unsafe)
	raw_args = flatten_learnset_sections(learnsets, move_symbol_to_id)
	parts = [encode_push_int(species)]
	getter_argc = 1
	if form is not None:
		parts.append(encode_push_int(form))
		getter_argc = 2
	parts.append(encode_call_ext_1("GetPiiProp", getter_argc))
	for value in raw_args:
		parts.append(encode_push_int(value))
	parts.append(encode_call_ext_1(setter_name, 1 + len(raw_args)))
	parts.append(encode_pop())
	return b"".join(parts)


def parse_block_records(instructions: List[dict]) -> List[BlockRecord]:
	stack = SimpleStack()
	call_records = []

	for instruction in instructions:
		opname = instruction["opname"]
		decoded = instruction.get("decoded", {})
		instruction_index = instruction["index"]

		if opname == "push_int":
			stack.push(StackValue("int", decoded["value_signed"], instruction_index))
		elif opname == "push_s_f":
			stack.push(StackValue("float", decoded["value"], instruction_index))
		elif opname == "push_str":
			stack.push(StackValue("string", decoded.get("value"), instruction_index))
		elif opname == "push_dat":
			stack.push(StackValue("data", decoded.get("value"), instruction_index))
		elif opname in ("access", "access_ptr"):
			stack.push(StackValue("expr", ("access", decoded["segment"], decoded["slot"]), instruction_index))
		elif opname in (
			"add",
			"sub",
			"multiply",
			"divide",
			"cmp_eq",
			"cmp_neq",
			"cmp_lt",
			"cmp_geq",
			"cmp_leq",
			"cmp_gt",
			"bitwise_and",
			"bitwise_xor",
			"bitwise_or",
			"bitwise_rshift",
			"bitwise_lshift",
		):
			stack.popn(2)
			stack.push(StackValue("expr", opname, instruction_index))
		elif opname in ("negate", "complement", "is_zero"):
			stack.popn(1)
			stack.push(StackValue("expr", opname, instruction_index))
		elif opname in ("store", "store_ptr", "pop"):
			stack.popn(1)
		elif opname in ("call_ext_0", "call_ext_1"):
			function_name = decoded.get("function_name")
			declared_argc = decoded.get("declared_argc")
			argc = decoded.get("resolved_argc_from_next_nop_0") if opname == "call_ext_1" else None
			if argc is None:
				argc = 0 if declared_argc == VARARGS_SENTINEL else declared_argc
			arg_values = stack.popn(argc)
			call_records.append({
				"instruction_index": instruction_index,
				"pc_units": instruction["pc_units"],
				"function_name": function_name,
				"args": arg_values,
				"argc": argc,
			})
			stack.push(StackValue("call", function_name, instruction_index))
		elif opname == "call_imm":
			stack.popn(1)
			stack.push(StackValue("expr", "call_imm", instruction_index))
		elif opname in ("function_prologue", "function_epilogue"):
			stack = SimpleStack()

	blocks: List[BlockRecord] = []
	call_index = 0
	while call_index < len(call_records):
		current = call_records[call_index]
		if current["function_name"] != "GetPiiProp":
			call_index += 1
			continue
		if call_index + 1 >= len(call_records):
			break
		next_call = call_records[call_index + 1]
		if next_call["function_name"] not in VALID_SETTER_NAMES:
			call_index += 1
			continue
		key_values = []
		push_instruction_indices = []
		valid_key = True
		for stack_value in current["args"]:
			if stack_value.kind != "int" or stack_value.source_instruction_index is None:
				valid_key = False
				break
			key_values.append(stack_value.value)
			push_instruction_indices.append(stack_value.source_instruction_index)
		if not valid_key or len(key_values) not in (1, 2):
			call_index += 1
			continue
		setter_arg_values = []
		valid_setter = True
		for position, stack_value in enumerate(next_call["args"]):
			if position == 0:
				if stack_value.kind != "call" or stack_value.source_instruction_index != current["instruction_index"]:
					valid_setter = False
					break
				continue
			if stack_value.kind != "int":
				valid_setter = False
				break
			setter_arg_values.append(stack_value.value)
		if not valid_setter:
			call_index += 1
			continue

		getter_nop_instruction_index = current["instruction_index"] + 1
		setter_nop_instruction_index = next_call["instruction_index"] + 1
		pop_instruction_index = setter_nop_instruction_index + 1
		if pop_instruction_index >= len(instructions) or instructions[pop_instruction_index]["opcode"] != PKCOpCode.pop.value:
			raise ValueError(f"Expected pop after setter call at PC 0x{next_call['pc_units']:04X}")

		blocks.append(BlockRecord(
			block_index=len(blocks),
			key_values=key_values,
			setter_name=next_call["function_name"],
			setter_arg_values=setter_arg_values,
			push_instruction_indices=push_instruction_indices,
			get_call_instruction_index=current["instruction_index"],
			getter_nop_instruction_index=getter_nop_instruction_index,
			setter_call_instruction_index=next_call["instruction_index"],
			setter_nop_instruction_index=setter_nop_instruction_index,
			pop_instruction_index=pop_instruction_index,
			instructions=instructions,
		))
		call_index += 2
	return blocks


def export_json_document(pkc_path: str) -> dict:
	pkc = read_pkc(pkc_path)
	string_entries = decode_ascii_cstring_pool(pkc["string_raw"], pkc["string_count"])
	if string_entries is None:
		string_entries = []
	data_section = decode_data_section(pkc["data_raw"], pkc["data_count"])
	instructions = decode_instruction_stream(pkc["code_raw"] + pkc["trailing_raw"], string_entries, data_section)
	blocks = parse_block_records(instructions)
	move_enum_map = load_move_enum_map()
	species_enum_map = load_species_enum_map()

	entries = []
	for block in blocks:
		species_symbol = species_id_to_symbol(block.species, species_enum_map)
		entries.append({
			"block_index": block.block_index,
			"key": species_symbol if block.form is None else f"{species_symbol}, {block.form}",
			"species": species_symbol,
			"form": block.form,
			"setter": block.setter_name,
			"pc_range": {
				"start": f"0x{block.start_pc_units:04X}",
				"getpiiprop": f"0x{block.get_call_pc_units:04X}",
				"setter": f"0x{block.setter_call_pc_units:04X}",
				"end": f"0x{block.end_pc_units:04X}",
			},
			"learnsets": export_learnsets_with_enums(block.learnsets, move_enum_map),
		})

	return {
		"format": FORMAT_VERSION,
		"source_file": pkc["basename"],
		"notes": {
			"edit_authoritative_data_under": "entries[].learnsets",
			"species_and_moves_use_enum_symbols": True,
			"storage_note": "The PKC stores learnsets per species/form block, not per move.",
			"learnsets_shape": "object keyed by learnset section name",
		},
		"entries": entries,
	}


def load_json_document(json_path: str) -> dict:
	with open(json_path, "r", encoding="utf-8") as infile:
		document = json.load(infile)
	if document.get("format") != FORMAT_VERSION:
		raise ValueError(f"Unsupported JSON format. Expected {FORMAT_VERSION!r}, got {document.get('format')!r}")
	entries = document.get("entries")
	if not isinstance(entries, list):
		raise ValueError("JSON document must contain an entries list")
	return document


def normalize_entry_for_build(entry: dict, species_symbol_to_id: Dict[str, int], move_symbol_to_id: Dict[str, int], unsafe: bool = False) -> dict:
	if not isinstance(entry, dict):
		raise ValueError("Each entry must be an object")
	species, form = normalize_species_form(entry, species_symbol_to_id)
	setter = entry.get("setter")
	if not isinstance(setter, str):
		setter = FORM_SETTER_NAME if form is not None else BASE_SETTER_NAME
	learnsets = entry.get("learnsets")
	if not isinstance(learnsets, dict):
		raise ValueError(f"Entry {entry.get('block_index')} must contain a learnsets object")
	entry_label = entry.get("key") or entry.get("block_index") or f"{species},{form}"
	validate_learnsets(learnsets, move_symbol_to_id, str(entry_label), unsafe=unsafe)
	flatten_learnset_sections(learnsets, move_symbol_to_id)
	return {
		"block_index": entry.get("block_index"),
		"species": species,
		"form": form,
		"setter": setter,
		"learnsets": learnsets,
	}


def split_extra_entries(extra_entries: List[dict]) -> Tuple[List[dict], List[dict]]:
	base_entries = []
	form_entries = []
	for entry in extra_entries:
		if entry["setter"] == BASE_SETTER_NAME:
			base_entries.append(entry)
		else:
			form_entries.append(entry)
	return base_entries, form_entries


def build_code_with_json_entries(original_pkc: dict, instructions: List[dict], original_blocks: List[BlockRecord], document: dict, unsafe: bool = False) -> bytes:
	move_symbol_to_id = invert_enum_map(load_move_enum_map())
	species_symbol_to_id = invert_enum_map(load_species_enum_map())
	entries = document["entries"]
	if not original_blocks:
		raise ValueError("No learnset blocks were found in the original PKC")

	original_block_by_index = {block.block_index: block for block in original_blocks}
	json_entry_by_block_index: Dict[int, dict] = {}
	extra_entries: List[dict] = []

	for raw_entry in entries:
		entry = normalize_entry_for_build(raw_entry, species_symbol_to_id, move_symbol_to_id, unsafe=unsafe)
		block_index = entry["block_index"]
		if isinstance(block_index, int) and block_index in original_block_by_index:
			json_entry_by_block_index[block_index] = entry
			continue
		extra_entries.append(entry)

	for block_index in original_block_by_index:
		if block_index not in json_entry_by_block_index:
			original = original_block_by_index[block_index]
			json_entry_by_block_index[block_index] = {
				"block_index": original.block_index,
				"species": original.species,
				"form": original.form,
				"setter": original.setter_name,
				"learnsets": original.learnsets,
			}

	first_block = original_blocks[0]
	last_block = original_blocks[-1]
	prefix_bytes = original_pkc["code_raw"][:first_block.start_byte_offset]
	suffix_bytes = original_pkc["code_raw"][last_block.end_byte_offset:]
	parts = [prefix_bytes]

	base_extras, form_extras = split_extra_entries(extra_entries)
	inserted_form_extras = False
	for block in original_blocks:
		if block.setter_name == FORM_SETTER_NAME and not inserted_form_extras:
			for entry in base_extras:
				parts.append(build_block_bytes(entry["species"], entry["form"], entry["setter"], entry["learnsets"], move_symbol_to_id, unsafe=unsafe))
			for entry in form_extras:
				parts.append(build_block_bytes(entry["species"], entry["form"], entry["setter"], entry["learnsets"], move_symbol_to_id, unsafe=unsafe))
			inserted_form_extras = True
		entry = json_entry_by_block_index[block.block_index]
		parts.append(build_block_bytes(entry["species"], entry["form"], entry["setter"], entry["learnsets"], move_symbol_to_id, unsafe=unsafe))
	if not inserted_form_extras:
		for entry in base_extras:
			parts.append(build_block_bytes(entry["species"], entry["form"], entry["setter"], entry["learnsets"], move_symbol_to_id, unsafe=unsafe))
		for entry in form_extras:
			parts.append(build_block_bytes(entry["species"], entry["form"], entry["setter"], entry["learnsets"], move_symbol_to_id, unsafe=unsafe))
	parts.append(suffix_bytes)
	return b"".join(parts)


def write_pkc(path: str, original_pkc: dict, rebuilt_code: bytes):
	header = bytearray()
	header.extend(original_pkc["magic"])
	header.extend(struct.pack(">7I",
		original_pkc["flags"],
		len(rebuilt_code),
		original_pkc["data_size"],
		original_pkc["data_count"],
		original_pkc["string_size"],
		original_pkc["string_count"],
		original_pkc["reserved"],
	))
	payload = bytes(header) + rebuilt_code + original_pkc["data_raw"] + original_pkc["string_raw"] + original_pkc["trailing_raw"]
	with open(path, "wb") as outfile:
		outfile.write(payload)


def command_export(args):
	document = export_json_document(args.pkc_path)
	output_path = args.output
	if not output_path:
		base, _ext = os.path.splitext(args.pkc_path)
		output_path = base + ".json"
	with open(output_path, "w", encoding="utf-8") as outfile:
		json.dump(document, outfile, ensure_ascii=False, indent="\t")
		outfile.write("\n")
	print(f"Exported {len(document['entries'])} learnset blocks to {output_path}")


def derive_rebuilt_output_path(original_pkc_path: str) -> str:
	base, ext = os.path.splitext(original_pkc_path)
	if not ext:
		ext = ".pkc"
	return f"{base}.rebuilt{ext}"


def command_import(args):
	original_pkc = read_pkc(args.original_pkc)
	string_entries = decode_ascii_cstring_pool(original_pkc["string_raw"], original_pkc["string_count"])
	if string_entries is None:
		string_entries = []
	data_section = decode_data_section(original_pkc["data_raw"], original_pkc["data_count"])
	instructions = decode_instruction_stream(original_pkc["code_raw"] + original_pkc["trailing_raw"], string_entries, data_section)
	original_blocks = parse_block_records(instructions)
	document = load_json_document(args.json_path)
	rebuilt_code = build_code_with_json_entries(original_pkc, instructions, original_blocks, document, unsafe=args.unsafe)
	output_pkc = args.output_pkc if args.output_pkc else derive_rebuilt_output_path(args.original_pkc)
	write_pkc(output_pkc, original_pkc, rebuilt_code)
	print(f"Imported {len(document['entries'])} JSON entries into {output_pkc}")


def command_verify(args):
	original_pkc = read_pkc(args.pkc_path)
	string_entries = decode_ascii_cstring_pool(original_pkc["string_raw"], original_pkc["string_count"])
	if string_entries is None:
		string_entries = []
	data_section = decode_data_section(original_pkc["data_raw"], original_pkc["data_count"])
	instructions = decode_instruction_stream(original_pkc["code_raw"] + original_pkc["trailing_raw"], string_entries, data_section)
	original_blocks = parse_block_records(instructions)
	document = export_json_document(args.pkc_path)
	rebuilt_code = build_code_with_json_entries(original_pkc, instructions, original_blocks, document)
	rebuilt_raw = bytes(original_pkc["magic"]) + struct.pack(">7I",
		original_pkc["flags"],
		len(rebuilt_code),
		original_pkc["data_size"],
		original_pkc["data_count"],
		original_pkc["string_size"],
		original_pkc["string_count"],
		original_pkc["reserved"],
	) + rebuilt_code + original_pkc["data_raw"] + original_pkc["string_raw"] + original_pkc["trailing_raw"]
	matches = rebuilt_raw == original_pkc["raw"]
	print(f"Blocks: {len(document['entries'])}")
	print(f"Match: {'YES' if matches else 'NO'}")
	if not matches:
		for index, (left, right) in enumerate(zip(rebuilt_raw, original_pkc["raw"])):
			if left != right:
				print(f"First mismatch at 0x{index:X}: rebuilt=0x{left:02X}, original=0x{right:02X}")
				break
		if len(rebuilt_raw) != len(original_pkc["raw"]):
			print(f"Length mismatch: rebuilt={len(rebuilt_raw)}, original={len(original_pkc['raw'])}")
		raise SystemExit(1)


def command_summary(args):
	pkc = read_pkc(args.pkc_path)
	string_entries = decode_ascii_cstring_pool(pkc["string_raw"], pkc["string_count"])
	if string_entries is None:
		string_entries = []
	data_section = decode_data_section(pkc["data_raw"], pkc["data_count"])
	instructions = decode_instruction_stream(pkc["code_raw"] + pkc["trailing_raw"], string_entries, data_section)
	blocks = parse_block_records(instructions)
	print(f"File: {pkc['basename']}")
	print(f"Flags: 0x{pkc['flags']:X}")
	print(f"Code size: {pkc['code_size']}")
	print(f"Blocks: {len(blocks)}")
	if blocks:
		print(f"First block PC: 0x{blocks[0].start_pc_units:04X}")
		print(f"Last block PC: 0x{blocks[-1].end_pc_units:04X}")


def build_arg_parser():
	parser = argparse.ArgumentParser(description="Export/import WazaTableData PKC learnsets as editable JSON with species and move enums in a clean learnsets object.")
	subparsers = parser.add_subparsers(dest="command", required=True)

	summary_parser = subparsers.add_parser("summary", help="Show a structural summary")
	summary_parser.add_argument("pkc_path")
	summary_parser.set_defaults(func=command_summary)

	export_parser = subparsers.add_parser("export", help="Export PKC to editable JSON")
	export_parser.add_argument("pkc_path")
	export_parser.add_argument("output", nargs="?")
	export_parser.set_defaults(func=command_export)

	import_parser = subparsers.add_parser("import", help="Import editable JSON back into a PKC")
	import_parser.add_argument("json_path")
	import_parser.add_argument("original_pkc")
	import_parser.add_argument("output_pkc", nargs="?", help="Optional output PKC path. Defaults to <original>.rebuilt<ext>")
	import_parser.add_argument("--unsafe", action="store_true", help="Bypass vanilla-count and ordering validation checks")
	import_parser.set_defaults(func=command_import)

	verify_parser = subparsers.add_parser("verify", help="Roundtrip check against the original PKC")
	verify_parser.add_argument("pkc_path")
	verify_parser.set_defaults(func=command_verify)

	return parser


def main():
	parser = build_arg_parser()
	args = parser.parse_args()
	args.func(args)


if __name__ == "__main__":
	main()
