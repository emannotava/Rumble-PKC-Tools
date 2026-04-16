#!/usr/bin/env python3
import argparse
import json
import math
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


FORMAT_VERSION = "piiprop-edit-json-2"
VARARGS_SENTINEL = 0xFFFFFFFF
SPECIES_ENUM_RE = re.compile(r"^SPECIES_(\d+)$")
MOVE_ENUM_RE = re.compile(r"^MOVE_(\d+)$")


SPECIES_ID_TO_SYMBOL = {int(member): member.name for member in SpeciesEnum}
SPECIES_SYMBOL_TO_ID = {member.name: int(member) for member in SpeciesEnum}
MOVE_ID_TO_SYMBOL = {int(member): member.name for member in MoveEnum}
MOVE_SYMBOL_TO_ID = {member.name: int(member) for member in MoveEnum}


def species_id_to_symbol(species: int) -> str:
	return SPECIES_ID_TO_SYMBOL.get(species, f"SPECIES_{species:04d}")



def move_id_to_symbol(move_id: int):
	return MOVE_ID_TO_SYMBOL.get(move_id, f"MOVE_{move_id:04d}") if move_id > 0 else move_id



def move_value_to_id(value):
	if isinstance(value, int):
		return value
	if isinstance(value, str):
		text = value.strip()
		if text in MOVE_SYMBOL_TO_ID:
			return MOVE_SYMBOL_TO_ID[text]
		match = MOVE_ENUM_RE.fullmatch(text)
		if match is not None:
			return int(match.group(1))
	return normalize_number(value)



def species_value_to_id(value):
	if isinstance(value, int):
		return value
	if isinstance(value, str):
		text = value.strip()
		if text in SPECIES_SYMBOL_TO_ID:
			return SPECIES_SYMBOL_TO_ID[text]
		match = SPECIES_ENUM_RE.fullmatch(text)
		if match is not None:
			return int(match.group(1))
	return normalize_number(value)



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


FIELD_NAMES = [
	"attack",
	"defense",
	"unknown_02",
	"unknown_03",
	"unknown_04",
	"boss_attack_multiplier",
	"boss_defense_multiplier",
	"boss_hp_multiplier",
	"boss_speed_override",
	"unknown_09",
	"used_by_cboss_unknown_role",
	"unknown_11",
	"unknown_12",
	"a_size_or_physical_size",
	"hp",
	"walk_speed_coeff_likely",
	"speed",
	"boss_scalar_x1_or_x3",
	"wild_move",
	"wild_move_2nd_unused",
]
PERCENT_FIELDS = {
	"boss_attack_multiplier",
	"boss_defense_multiplier",
	"boss_hp_multiplier",
	"a_size_or_physical_size",
	"walk_speed_coeff_likely",
}
SETTER_NAMES = {"method_pprSetAllForAllFormno", "method_pprSetAll"}

FUNCTION_INDEX_BY_NAME = {}
for function_index, pair in enumerate(script_functions):
	if not isinstance(pair, tuple) or len(pair) != 2:
		continue
	function_name, _declared_argc = pair
	if function_name and function_name not in FUNCTION_INDEX_BY_NAME:
		FUNCTION_INDEX_BY_NAME[function_name] = function_index



def opcode_name(opcode):
	try:
		return PKCOpCode(opcode).name
	except ValueError:
		return f"opcode_0x{opcode:02X}"



def sign24(value):
	return value - 0x1000000 if value & 0x800000 else value



def hex_bytes(data):
	return data.hex().upper()



def decode_ascii_cstring_pool(raw, expected_count):
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



def encode_ascii_cstring_pool(entries):
	out = bytearray()
	for entry in entries:
		out.extend(entry.encode("ascii"))
		out.append(0)
	return bytes(out)



def decode_utf16be_cstring_pool(raw, expected_count):
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



def encode_utf16be_cstring_pool(entries):
	out = bytearray()
	for entry in entries:
		out.extend(entry.encode("utf-16-be"))
		out.extend(b"\x00\x00")
	return bytes(out)



def decode_data_section(raw, expected_count):
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



def encode_data_section(section):
	encoding = section.get("encoding", "raw")
	if encoding == "utf16be_cstring_pool":
		entries = section.get("entries", [])
		return encode_utf16be_cstring_pool(entries), len(entries)
	if encoding == "raw":
		raw_hex = section.get("raw_hex", "")
		return bytes.fromhex(raw_hex), int(section.get("count", 0))
	raise ValueError(f"Unsupported data_section encoding: {encoding}")



def read_pkc(path):
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
		"magic_hex": hex_bytes(magic),
		"flags": flags,
		"code_size": code_size,
		"data_size": data_size,
		"data_count": data_count,
		"string_size": string_size,
		"string_count": string_count,
		"reserved": reserved,
		"code_raw": raw[code_start:data_start],
		"data_raw": raw[data_start:string_start],
		"string_raw": raw[string_start:trailing_start],
		"trailing_raw": raw[trailing_start:],
	}



def decode_instruction_stream(code_raw, string_entries, data_section):
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
			"imm_hex": f"0x{imm:06X}",
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
				"value_hex": f"0x{payload_value:08X}",
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



def decode_pkc(path):
	container = read_pkc(path)

	string_entries = decode_ascii_cstring_pool(container["string_raw"], container["string_count"])
	if string_entries is None:
		string_section = {
			"encoding": "raw",
			"entries": [],
			"raw_hex": hex_bytes(container["string_raw"]),
			"count": container["string_count"],
		}
		string_entries = []
	else:
		string_section = {
			"encoding": "ascii_cstring_pool",
			"entries": string_entries,
			"raw_hex": hex_bytes(container["string_raw"]),
			"count": len(string_entries),
		}

	data_section = decode_data_section(container["data_raw"], container["data_count"])

	logical_code_raw = container["code_raw"]
	trailing_raw = container["trailing_raw"]
	code_continues_in_trailing = False

	if container["data_size"] == 0 and container["string_size"] == 0 and trailing_raw:
		logical_code_raw = container["code_raw"] + trailing_raw
		trailing_raw = b""
		code_continues_in_trailing = True

	instructions = decode_instruction_stream(logical_code_raw, string_entries, data_section)

	return {
		"format": "pkc-json-1",
		"source_basename": container["basename"],
		"header": {
			"magic_hex": container["magic_hex"],
			"flags": container["flags"],
			"reserved": container["reserved"],
		},
		"section_sizes_from_source": {
			"code_size": container["code_size"],
			"data_size": container["data_size"],
			"data_count": container["data_count"],
			"string_size": container["string_size"],
			"string_count": container["string_count"],
			"trailing_size": len(container["trailing_raw"]),
		},
		"physical_layout": {
			"code_continues_in_trailing": code_continues_in_trailing,
			"declared_code_size": container["code_size"],
		},
		"code_section": {
			"logical_code_size": len(logical_code_raw),
			"instructions": instructions,
		},
		"data_section": data_section,
		"string_section": string_section,
		"trailing_raw_hex": hex_bytes(trailing_raw),
	}



def encode_instruction(instruction):
	opcode = int(instruction["opcode"])
	imm = int(instruction["imm"])
	if not 0 <= opcode <= 0xFF:
		raise ValueError(f"Opcode out of range: {opcode}")
	if not 0 <= imm <= 0xFFFFFF:
		raise ValueError(f"Immediate out of range for 24-bit field: {imm}")

	out = bytearray()
	out.append(opcode)
	out.extend(imm.to_bytes(3, "big"))

	payload_hex = instruction.get("payload_hex", "")
	payload = bytes.fromhex(payload_hex) if payload_hex else b""

	if opcode == PKCOpCode.push_s_f.value:
		if len(payload) != 4:
			raise ValueError("push_s_f payload must be exactly 4 bytes")
		out.extend(payload)
	elif opcode == PKCOpCode.function_prologue.value:
		if len(payload) != 4:
			raise ValueError("function_prologue payload must be exactly 4 bytes")
		out.extend(payload)
	elif opcode == PKCOpCode.switch.value:
		case_count = imm >> 16
		expected = case_count * 2 + (2 if case_count & 1 else 0)
		if len(payload) != expected:
			raise ValueError(f"switch payload length mismatch: expected {expected} bytes for {case_count} cases, got {len(payload)}")
		out.extend(payload)
	else:
		if payload:
			raise ValueError(f"Opcode 0x{opcode:02X} does not use payload_hex, but payload_hex was provided")

	return bytes(out)



def encode_pkc_document(document):
	if document.get("format") != "pkc-json-1":
		raise ValueError(f"Unsupported JSON format: {document.get('format')!r}")

	magic = bytes.fromhex(document["header"]["magic_hex"])
	if len(magic) != 4:
		raise ValueError("header.magic_hex must encode exactly 4 bytes")

	flags = int(document["header"]["flags"])
	reserved = int(document["header"].get("reserved", 0))

	code_bytes = bytearray()
	for instruction in document["code_section"]["instructions"]:
		code_bytes.extend(encode_instruction(instruction))
	code_bytes = bytes(code_bytes)

	data_bytes, data_count = encode_data_section(document["data_section"])

	string_section = document["string_section"]
	string_encoding = string_section.get("encoding", "ascii_cstring_pool")
	if string_encoding == "ascii_cstring_pool":
		string_entries = string_section.get("entries", [])
		string_bytes = encode_ascii_cstring_pool(string_entries)
		string_count = len(string_entries)
	elif string_encoding == "raw":
		string_bytes = bytes.fromhex(string_section.get("raw_hex", ""))
		string_count = int(string_section.get("count", 0))
	else:
		raise ValueError(f"Unsupported string_section encoding: {string_encoding}")

	trailing_bytes = bytes.fromhex(document.get("trailing_raw_hex", ""))
	physical_layout = document.get("physical_layout", {})
	code_continues_in_trailing = bool(physical_layout.get("code_continues_in_trailing", False))
	declared_code_size = int(physical_layout.get("declared_code_size", len(code_bytes)))

	if code_continues_in_trailing:
		if declared_code_size > len(code_bytes):
			raise ValueError(f"Logical code is too short to satisfy declared_code_size {declared_code_size}")
		stored_code_bytes = code_bytes[:declared_code_size]
		trailing_bytes = code_bytes[declared_code_size:] + trailing_bytes
		code_size_for_header = declared_code_size
	else:
		stored_code_bytes = code_bytes
		code_size_for_header = len(stored_code_bytes)

	header = struct.pack(">7I", flags, code_size_for_header, len(data_bytes), data_count, len(string_bytes), string_count, reserved)
	return magic + header + stored_code_bytes + data_bytes + string_bytes + trailing_bytes



def resolve_call_argc(instruction: dict) -> Optional[int]:
	decoded = instruction.get("decoded", {})
	argc = decoded.get("resolved_argc_from_next_nop_0")
	if argc is not None:
		return int(argc)
	argc = decoded.get("declared_argc")
	if argc is None:
		return None
	return int(argc)



def get_function_name(instruction: dict) -> Optional[str]:
	decoded = instruction.get("decoded", {})
	return decoded.get("function_name")



def parse_numeric_push(instruction: dict):
	opcode = int(instruction["opcode"])
	decoded = instruction.get("decoded", {})
	if opcode == PKCOpCode.push_int.value:
		return int(decoded["value_signed"]), "int"
	if opcode == PKCOpCode.push_s_f.value:
		return float(decoded["value"]), "float"
	return None



def is_getpiiprop_call(instruction: dict) -> bool:
	if int(instruction["opcode"]) not in (PKCOpCode.call_ext_0.value, PKCOpCode.call_ext_1.value):
		return False
	return get_function_name(instruction) == "GetPiiProp"



def is_setter_call(instruction: dict) -> bool:
	if int(instruction["opcode"]) not in (PKCOpCode.call_ext_0.value, PKCOpCode.call_ext_1.value):
		return False
	return get_function_name(instruction) in SETTER_NAMES



def format_percent_value(value) -> str:
	numeric = float(value) * 100.0
	text = format(numeric, ".6f").rstrip("0").rstrip(".")
	if text in ("", "-0"):
		text = "0"
	return f"{text}%"



def field_value_for_export(field_name: str, value):
	if field_name in PERCENT_FIELDS:
		return format_percent_value(value)
	if field_name in {"wild_move", "wild_move_2nd_unused"}:
		return move_id_to_symbol(int(value)) if isinstance(value, int) else value
	return value



def field_value_for_import(field_name: str, value):
	if field_name in PERCENT_FIELDS:
		if isinstance(value, str):
			text = value.strip()
			if text.endswith("%"):
				return normalize_number(text[:-1]) / 100.0
		return normalize_number(value)
	if field_name in {"wild_move", "wild_move_2nd_unused"}:
		return move_value_to_id(value)
	return normalize_number(value)



def build_fields_dict(values: List[float]) -> Dict[str, float]:
	return {FIELD_NAMES[index]: field_value_for_export(FIELD_NAMES[index], values[index]) for index in range(len(FIELD_NAMES))}



def normalize_number(value):
	if isinstance(value, bool):
		return int(value)
	if isinstance(value, int):
		return value
	if isinstance(value, float):
		if not math.isfinite(value):
			raise ValueError(f"Non-finite float is not allowed in JSON: {value!r}")
		return value
	if isinstance(value, str):
		text = value.strip()
		if not text:
			raise ValueError("Empty string is not a valid numeric value")
		try:
			if any(ch in text for ch in ".eE"):
				parsed = float(text)
				if parsed.is_integer() and "." not in text and "e" not in text.lower():
					return int(parsed)
				return parsed
			return int(text, 10)
		except ValueError as exc:
			raise ValueError(f"Invalid numeric text: {value!r}") from exc
	raise ValueError(f"Unsupported numeric type: {type(value).__name__}")



def collect_entry_values(entry: dict) -> List[float]:
	fields = entry.get("fields")
	if not isinstance(fields, dict):
		raise ValueError(f"Entry {entry.get('block_index')} must have a fields object")
	values = []
	for field_name in FIELD_NAMES:
		lookup_name = field_name
		if field_name == "wild_move" and field_name not in fields and "unknown_18" in fields:
			lookup_name = "unknown_18"
		if field_name == "wild_move_2nd_unused" and field_name not in fields and "unknown_19" in fields:
			lookup_name = "unknown_19"
		if lookup_name not in fields:
			raise ValueError(f"Entry {entry.get('block_index')} is missing field {field_name!r}")
		values.append(field_value_for_import(field_name, fields[lookup_name]))
	return values



def parse_piiprop_blocks(document: dict) -> List[dict]:
	instructions = document["code_section"]["instructions"]
	blocks = []
	index = 0
	block_index = 1

	while index < len(instructions):
		instruction = instructions[index]
		if not is_getpiiprop_call(instruction):
			index += 1
			continue

		argc = resolve_call_argc(instruction)
		if argc not in (1, 2):
			index += 1
			continue

		push_start = index - argc
		if push_start < 0:
			index += 1
			continue

		prop_args = []
		prop_arg_push_indexes = []
		valid = True
		for push_index in range(push_start, index):
			parsed = parse_numeric_push(instructions[push_index])
			if parsed is None:
				valid = False
				break
			value, kind = parsed
			if kind != "int":
				valid = False
				break
			prop_args.append(value)
			prop_arg_push_indexes.append(push_index)
		if not valid:
			index += 1
			continue

		next_index = index + 1
		if next_index >= len(instructions) or int(instructions[next_index]["opcode"]) != PKCOpCode.nop_0.value:
			index += 1
			continue

		value_push_indexes = []
		value_push_types = []
		values = []
		cursor = next_index + 1
		while cursor < len(instructions):
			candidate = instructions[cursor]
			if is_setter_call(candidate):
				break
			parsed = parse_numeric_push(candidate)
			if parsed is None:
				valid = False
				break
			value, value_type = parsed
			values.append(value)
			value_push_types.append(value_type)
			value_push_indexes.append(cursor)
			cursor += 1

		if not valid or cursor >= len(instructions):
			index += 1
			continue

		setter_instruction = instructions[cursor]
		setter_name = get_function_name(setter_instruction)
		setter_argc = resolve_call_argc(setter_instruction)
		if setter_name not in SETTER_NAMES or setter_argc != 21 or len(values) != 20:
			index += 1
			continue

		after_setter = cursor + 1
		if after_setter >= len(instructions) or int(instructions[after_setter]["opcode"]) != PKCOpCode.nop_0.value:
			index += 1
			continue
		after_nop = after_setter + 1
		if after_nop >= len(instructions) or int(instructions[after_nop]["opcode"]) != PKCOpCode.pop.value:
			index += 1
			continue

		species = int(prop_args[0])
		form = int(prop_args[1]) if argc == 2 else None
		blocks.append({
			"block_index": block_index,
			"species": species,
			"form": form,
			"setter": setter_name,
			"start_pc": int(instructions[push_start]["pc_units"]),
			"getpiiprop_pc": int(instruction["pc_units"]),
			"setter_pc": int(setter_instruction["pc_units"]),
			"end_pc": int(instructions[after_nop]["pc_units"]),
			"start_instruction_index": push_start,
			"prop_arg_push_indexes": prop_arg_push_indexes,
			"getpiiprop_index": index,
			"value_push_indexes": value_push_indexes,
			"value_push_types": value_push_types,
			"setter_index": cursor,
			"end_instruction_index": after_nop,
			"values": values,
			"fields": build_fields_dict(values),
		})
		block_index += 1
		index = after_nop + 1

	return blocks



def build_export_document(pkc_path: str) -> dict:
	document = decode_pkc(pkc_path)
	blocks = parse_piiprop_blocks(document)
	entries = []
	for block in blocks:
		species_symbol = species_id_to_symbol(block["species"])
		entries.append({
			"block_index": block["block_index"],
			"key": species_symbol if block["form"] is None else f"{species_symbol},{block['form']}",
			"species": species_symbol,
			"form": block["form"],
			"setter": block["setter"],
			"pc_range": {
				"start": f"0x{block['start_pc']:04X}",
				"getpiiprop": f"0x{block['getpiiprop_pc']:04X}",
				"setter": f"0x{block['setter_pc']:04X}",
				"end": f"0x{block['end_pc']:04X}",
			},
			"fields": block["fields"],
		})

	return {
		"format": FORMAT_VERSION,
		"source_pkc": os.path.basename(pkc_path),
		"field_order": [{"index": index, "name": name} for index, name in enumerate(FIELD_NAMES)],
		"notes": {
			"editing": [
				"Edit values only through the named fields object.",
				"Existing blocks are matched by block_index.",
				"species uses SpeciesEnum symbols from pyPKCEnumLib.py.",
				"Multiplier and coeff fields export as percentages and accept values like 125% on import.",
				"wild_move and wild_move_2nd_unused export as MoveEnum symbols from pyPKCEnumLib.py when possible and also accept MoveEnum symbols on import.",
				"For a brand-new block, either omit block_index or use a new unused integer.",
				"For base/all-form entries, keep form as null and setter as method_pprSetAllForAllFormno.",
				"For form-specific override entries, keep form as an integer and setter as method_pprSetAll.",
				"Adding new blocks is supported by this importer.",
				"Removing original blocks is not supported by this importer.",
			],
			"known_fields": {
				"base_ap": 0,
				"base_dp": 1,
				"boss_ap_multiplier_or_scalar": 5,
				"boss_dp_multiplier_or_scalar": 6,
				"boss_hp_multiplier_or_scalar": 7,
				"boss_spd_override_or_scalar": 8,
				"used_by_cboss_unknown_role": 10,
				"a_size_or_physical_size": 13,
				"base_hp": 14,
				"walk_speed_coeff_likely": 15,
				"base_spd": 16,
				"boss_scalar_x1_or_x3": 17,
				"wild_move": 18,
				"wild_move_2nd_unused": 19,
			},
		},
		"entries": entries,
	}



def assign_push_value(instruction: dict, value, preferred_type: str) -> None:
	if isinstance(value, float) and value.is_integer() and preferred_type == "int":
		value = int(value)

	if isinstance(value, int):
		if not -0x800000 <= value <= 0x7FFFFF:
			raise ValueError(f"Integer value out of signed 24-bit range: {value}")
		instruction["opcode"] = PKCOpCode.push_int.value
		instruction["opname"] = PKCOpCode.push_int.name
		instruction["imm"] = value & 0xFFFFFF
		instruction["imm_hex"] = f"0x{instruction['imm']:06X}"
		instruction["payload_hex"] = ""
		instruction["length_bytes"] = 4
		instruction["length_units"] = 1
		instruction["decoded"] = {
			"type": "int24",
			"value_signed": value,
			"value_unsigned": value & 0xFFFFFF,
		}
		return

	float_value = float(value)
	instruction["opcode"] = PKCOpCode.push_s_f.value
	instruction["opname"] = PKCOpCode.push_s_f.name
	instruction["imm"] = 0
	instruction["imm_hex"] = f"0x{instruction['imm']:06X}"
	instruction["payload_hex"] = struct.pack(">f", float_value).hex().upper()
	instruction["length_bytes"] = 8
	instruction["length_units"] = 2
	instruction["decoded"] = {
		"type": "float",
		"value": float_value,
	}



def make_instruction(opcode: int, imm: int = 0, payload: bytes = b"", decoded: Optional[dict] = None) -> dict:
	instruction = {
		"opcode": int(opcode),
		"opname": opcode_name(int(opcode)),
		"imm": int(imm),
		"imm_hex": f"0x{int(imm):06X}",
		"payload_hex": payload.hex().upper() if payload else "",
	}
	if opcode in (PKCOpCode.push_s_f.value, PKCOpCode.function_prologue.value):
		instruction["length_bytes"] = 8
		instruction["length_units"] = 2
	else:
		instruction["length_bytes"] = 4 + len(payload)
		instruction["length_units"] = 1
	if decoded is not None:
		instruction["decoded"] = decoded
	return instruction



def make_push_instruction(value):
	temp = {}
	preferred_type = "int" if isinstance(value, int) and not isinstance(value, bool) else "float"
	assign_push_value(temp, normalize_number(value), preferred_type)
	return temp



def make_call_ext_1_instruction(function_name: str, argc: int) -> List[dict]:
	if function_name not in FUNCTION_INDEX_BY_NAME:
		raise ValueError(f"Function name {function_name!r} was not found in script_functions.py")
	function_index = FUNCTION_INDEX_BY_NAME[function_name]
	return [
		make_instruction(
			PKCOpCode.call_ext_1.value,
			function_index,
			decoded={
				"type": "external_call",
				"function_index": function_index,
				"function_name": function_name,
				"declared_argc": script_functions[function_index][1],
				"resolved_argc_from_next_nop_0": argc,
			},
		),
		make_instruction(PKCOpCode.nop_0.value, argc),
	]



def normalize_species_and_form(entry: dict, for_existing_form: Optional[int]) -> Tuple[int, Optional[int]]:
	species = species_value_to_id(entry["species"])
	if not isinstance(species, int):
		raise ValueError(f"Entry {entry.get('block_index')}: species must resolve to an integer")

	form_value = entry.get("form")
	if for_existing_form is None:
		if form_value is not None:
			raise ValueError(f"Entry {entry.get('block_index')}: base/all-form block must keep form as null")
		return species, None

	if form_value is None:
		raise ValueError(f"Entry {entry.get('block_index')}: form-specific block must keep a numeric form")
	form = normalize_number(form_value)
	if not isinstance(form, int):
		raise ValueError(f"Entry {entry.get('block_index')}: form must be an integer")
	return species, form



def infer_or_validate_new_entry_setter(entry: dict, species: int, form: Optional[int]) -> str:
	setter = entry.get("setter")
	if setter not in SETTER_NAMES:
		setter = "method_pprSetAllForAllFormno" if form is None else "method_pprSetAll"

	if setter == "method_pprSetAllForAllFormno" and form is not None:
		raise ValueError(f"New entry for species {species}, form {form}: method_pprSetAllForAllFormno requires form=null")
	if setter == "method_pprSetAll" and form is None:
		raise ValueError(f"New entry for species {species}: method_pprSetAll requires a numeric form")
	return setter



def synthesize_new_block_instructions(entry: dict) -> Tuple[str, List[dict], Tuple[int, Optional[int]]]:
	species = species_value_to_id(entry["species"])
	if not isinstance(species, int):
		raise ValueError(f"New entry {entry.get('block_index')}: species must resolve to an integer")

	form_raw = entry.get("form")
	form = None
	if form_raw is not None:
		form = normalize_number(form_raw)
		if not isinstance(form, int):
			raise ValueError(f"New entry {entry.get('block_index')}: form must be an integer when provided")

	setter = infer_or_validate_new_entry_setter(entry, species, form)
	values = collect_entry_values(entry)

	new_instructions = []
	new_instructions.append(make_push_instruction(species))
	if form is not None:
		new_instructions.append(make_push_instruction(form))
	new_instructions.extend(make_call_ext_1_instruction("GetPiiProp", 2 if form is not None else 1))
	for value in values:
		new_instructions.append(make_push_instruction(value))
	new_instructions.extend(make_call_ext_1_instruction(setter, 21))
	new_instructions.append(make_instruction(PKCOpCode.pop.value, 0))
	return setter, new_instructions, (species, form)



def find_exit_instruction_index(instructions: List[dict]) -> int:
	for index in range(len(instructions) - 1, -1, -1):
		if int(instructions[index]["opcode"]) == PKCOpCode.exit.value:
			return index
	raise ValueError("Could not find exit instruction in PKC")



def apply_json_to_document(pkc_document: dict, edit_document: dict) -> dict:
	if edit_document.get("format") != FORMAT_VERSION:
		raise ValueError(f"Unsupported JSON format: {edit_document.get('format')!r}")

	blocks = parse_piiprop_blocks(pkc_document)
	blocks_by_index = {block["block_index"]: block for block in blocks}
	entries = edit_document.get("entries")
	if not isinstance(entries, list):
		raise ValueError("JSON is missing entries list")

	instructions = pkc_document["code_section"]["instructions"]
	seen_existing = set()
	final_keys = {}
	new_base_blocks = []
	new_form_blocks = []

	for entry_pos, entry in enumerate(entries):
		if not isinstance(entry, dict):
			raise ValueError(f"Entry at index {entry_pos} is not an object")

		block_index_raw = entry.get("block_index")
		is_existing = False
		block_index = None
		if block_index_raw is not None:
			block_index = normalize_number(block_index_raw)
			if not isinstance(block_index, int):
				raise ValueError(f"Entry at index {entry_pos}: block_index must be an integer or null")
			is_existing = block_index in blocks_by_index

		if is_existing:
			if block_index in seen_existing:
				raise ValueError(f"Duplicate existing block_index in JSON: {block_index}")
			seen_existing.add(block_index)
			block = blocks_by_index[block_index]

			species, form = normalize_species_and_form(entry, block["form"])
			setter = entry.get("setter")
			if setter != block["setter"]:
				raise ValueError(f"Entry {block_index}: setter changed from {block['setter']} to {setter}")
			values = collect_entry_values(entry)

			arg_push_indexes = block["prop_arg_push_indexes"]
			assign_push_value(instructions[arg_push_indexes[0]], species, "int")
			if block["form"] is not None:
				assign_push_value(instructions[arg_push_indexes[1]], form, "int")

			for value_index, instruction_index in enumerate(block["value_push_indexes"]):
				preferred_type = block["value_push_types"][value_index]
				assign_push_value(instructions[instruction_index], values[value_index], preferred_type)

			key = (species, form)
			if key in final_keys:
				raise ValueError(f"Duplicate species/form key after edits: {species}" if form is None else f"Duplicate species/form key after edits: {species},{form}")
			final_keys[key] = ("existing", block_index)
			continue

		setter, new_instructions, key = synthesize_new_block_instructions(entry)
		if key in final_keys:
			species, form = key
			raise ValueError(f"Duplicate species/form key in added blocks: {species}" if form is None else f"Duplicate species/form key in added blocks: {species},{form}")
		final_keys[key] = ("new", block_index)
		if setter == "method_pprSetAllForAllFormno":
			new_base_blocks.extend(new_instructions)
		else:
			new_form_blocks.extend(new_instructions)

	if len(seen_existing) != len(blocks_by_index):
		missing = sorted(set(blocks_by_index) - seen_existing)
		raise ValueError(f"JSON does not contain all original blocks. Missing block_index values: {missing[:10]}{'...' if len(missing) > 10 else ''}")

	first_form_override_start = None
	for block in blocks:
		if block["setter"] == "method_pprSetAll":
			first_form_override_start = block["start_instruction_index"]
			break

	exit_index = find_exit_instruction_index(instructions)
	base_insert_index = first_form_override_start if first_form_override_start is not None else exit_index
	form_insert_index = exit_index

	pkc_document["code_section"]["instructions"] = (
		instructions[:base_insert_index]
		+ new_base_blocks
		+ instructions[base_insert_index:form_insert_index]
		+ new_form_blocks
		+ instructions[form_insert_index:]
	)
	return pkc_document



def command_export(args):
	export_document = build_export_document(args.input)
	output = args.output
	if output is None:
		base, _ = os.path.splitext(args.input)
		output = base + ".json"
	with open(output, "w", encoding="utf-8", newline="\n") as outfile:
		json.dump(export_document, outfile, indent="\t", ensure_ascii=False)
		outfile.write("\n")
	print(f"Wrote {output}")
	print(f"Entries: {len(export_document['entries'])}")



def command_import(args):
	with open(args.input, "r", encoding="utf-8") as infile:
		edit_document = json.load(infile)
	pkc_document = decode_pkc(args.source_pkc)
	updated_document = apply_json_to_document(pkc_document, edit_document)
	pkc_bytes = encode_pkc_document(updated_document)
	output = args.output
	if output is None:
		base, _ = os.path.splitext(args.input)
		output = base + ".rebuilt.pkc"
	with open(output, "wb") as outfile:
		outfile.write(pkc_bytes)
	print(f"Wrote {output}")



def command_verify(args):
	export_document = build_export_document(args.input)
	pkc_document = decode_pkc(args.input)
	updated_document = apply_json_to_document(pkc_document, export_document)
	rebuilt = encode_pkc_document(updated_document)
	with open(args.input, "rb") as infile:
		original = infile.read()
	if rebuilt != original:
		print("Roundtrip through editable JSON FAILED", file=sys.stderr)
		for index, (left, right) in enumerate(zip(original, rebuilt)):
			if left != right:
				print(f"First difference at 0x{index:X}: original=0x{left:02X}, rebuilt=0x{right:02X}", file=sys.stderr)
				break
		if len(original) != len(rebuilt):
			print(f"Original size={len(original)}, rebuilt size={len(rebuilt)}", file=sys.stderr)
		raise SystemExit(1)
	print("Roundtrip through editable JSON OK")
	print(f"Entries: {len(export_document['entries'])}")



def build_arg_parser():
	parser = argparse.ArgumentParser(description="Export PiiPropData PKC blocks to editable JSON and rebuild from edited JSON.")
	subparsers = parser.add_subparsers(dest="command", required=True)

	export_parser = subparsers.add_parser("export", help="Export PiiPropData PKC to editable JSON")
	export_parser.add_argument("input", help="Input PiiPropData PKC file")
	export_parser.add_argument("output", nargs="?", help="Output editable JSON file")
	export_parser.set_defaults(func=command_export)

	import_parser = subparsers.add_parser("import", help="Rebuild a PiiPropData PKC from edited JSON")
	import_parser.add_argument("input", help="Input editable JSON file")
	import_parser.add_argument("source_pkc", help="Original source PiiPropData PKC file to patch")
	import_parser.add_argument("output", nargs="?", help="Output rebuilt PKC file")
	import_parser.set_defaults(func=command_import)

	verify_parser = subparsers.add_parser("verify", help="Export then immediately re-import and compare bytes")
	verify_parser.add_argument("input", help="Input PiiPropData PKC file")
	verify_parser.set_defaults(func=command_verify)

	return parser



def main():
	parser = build_arg_parser()
	args = parser.parse_args()
	args.func(args)


if __name__ == "__main__":
	main()
