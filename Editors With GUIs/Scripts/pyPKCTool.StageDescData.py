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
except ImportError as exc:
	print(f"Failed to import script_functions.py: {exc}", file=sys.stderr)
	sys.exit(1)

try:
	from pyPKCEnumLib import DifficultyEnum, RankEnum, SpeciesEnum, StageEnum
except ImportError as exc:
	print(f"Failed to import pyPKCEnumLib.py: {exc}", file=sys.stderr)
	sys.exit(1)

FORMAT_VERSION = "stagedesc-edit-json-1"
STAGE_PARAM_NAMES = [
	"bgm_id",
	"unknown_01",
	"unknown_02",
	"map_desc_id",
	"env_se_id",
	"species_primary",
	"species_secondary",
	"species_tertiary",
]
MAP_PARAM_NAMES = [
	"unknown_00",
	"unknown_01",
	"unknown_02",
	"unknown_03",
]
MAP_INIT_NAMES = [
	"row_count",
	"unknown_01",
]
MAP_ROW_FIELD_NAMES = [
	"species_primary",
	"species_secondary",
	"species_tertiary",
	"weight",
	"unknown_05",
	"unknown_06",
	"unknown_07",
]
SPECIES_FIELD_NAMES = {"species_primary", "species_secondary", "species_tertiary"}
VARARGS_SENTINEL = 0xFFFFFFFF
NULLABLE_SPECIES_FIELD_NAMES = {"species_secondary", "species_tertiary"}
DIFFICULTY_ENUM_RE = re.compile(r"^DIFFICULTY_(\d+)$")
STAGE_ENUM_RE = re.compile(r"^MAP_(\d+)$")
RANK_ENUM_RE = re.compile(r"^RANK_(\d+)$")
SPECIES_ENUM_RE = re.compile(r"^SPECIES_(\d+)$")

DIFFICULTY_ID_TO_SYMBOL = {member.value: member.name for member in DifficultyEnum}
STAGE_ID_TO_SYMBOL = {member.value: member.name for member in StageEnum}
RANK_ID_TO_SYMBOL = {member.value: member.name for member in RankEnum}
SPECIES_ID_TO_SYMBOL = {member.value: member.name for member in SpeciesEnum}

DIFFICULTY_SYMBOL_TO_ID = {member.name: member.value for member in DifficultyEnum}
STAGE_SYMBOL_TO_ID = {member.name: member.value for member in StageEnum}
RANK_SYMBOL_TO_ID = {member.name: member.value for member in RankEnum}
SPECIES_SYMBOL_TO_ID = {member.name: member.value for member in SpeciesEnum}


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


def sign24(value: int) -> int:
	return value - 0x1000000 if value & 0x800000 else value


def hex_bytes(data: bytes) -> str:
	return data.hex().upper()


def read_pkc(path: str) -> dict:
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
		"reserved": reserved,
		"code_size": code_size,
		"data_size": data_size,
		"data_count": data_count,
		"string_size": string_size,
		"string_count": string_count,
		"code_raw": raw[code_start:data_start],
		"data_raw": raw[data_start:string_start],
		"string_raw": raw[string_start:trailing_start],
		"trailing_raw": raw[trailing_start:],
	}


def decode_instruction_stream(code_raw: bytes) -> List[dict]:
	instructions = []
	byte_offset = 0
	pc_units = 0

	while byte_offset < len(code_raw):
		if byte_offset + 4 > len(code_raw):
			raise ValueError(f"Truncated instruction header at byte offset 0x{byte_offset:X}")

		start = byte_offset
		opcode = code_raw[byte_offset]
		imm = int.from_bytes(code_raw[byte_offset + 1:byte_offset + 4], "big")
		byte_offset += 4
		payload = b""
		length_units = 1

		if opcode in (PKCOpCode.push_s_f.value, PKCOpCode.function_prologue.value):
			if byte_offset + 4 > len(code_raw):
				raise ValueError(f"Truncated 4-byte payload at byte offset 0x{byte_offset:X}")
			payload = code_raw[byte_offset:byte_offset + 4]
			byte_offset += 4
			length_units = 2
		elif opcode == PKCOpCode.switch.value:
			case_count = imm >> 16
			payload_size = case_count * 2 + (2 if case_count & 1 else 0)
			if byte_offset + payload_size > len(code_raw):
				raise ValueError(f"Truncated switch payload at byte offset 0x{byte_offset:X}")
			payload = code_raw[byte_offset:byte_offset + payload_size]
			byte_offset += payload_size

		instruction = {
			"index": len(instructions),
			"pc_units": pc_units,
			"byte_offset": start,
			"opcode": opcode,
			"imm": imm,
			"payload_hex": payload.hex().upper() if payload else "",
			"length_bytes": byte_offset - start,
			"length_units": length_units,
		}

		if opcode == PKCOpCode.push_int.value:
			instruction["push_type"] = "int"
			instruction["push_value"] = sign24(imm)
		elif opcode == PKCOpCode.push_s_f.value:
			instruction["push_type"] = "float"
			instruction["push_value"] = struct.unpack(">f", payload)[0]
		elif opcode in (PKCOpCode.call_ext_0.value, PKCOpCode.call_ext_1.value):
			function_name, declared_argc = script_functions[imm]
			instruction["function_name"] = function_name
			instruction["declared_argc"] = declared_argc

		instructions.append(instruction)
		pc_units += length_units

	for index, instruction in enumerate(instructions):
		if instruction["opcode"] == PKCOpCode.call_ext_1.value:
			next_instruction = instructions[index + 1] if index + 1 < len(instructions) else None
			if next_instruction and next_instruction["opcode"] == PKCOpCode.nop_0.value:
				instruction["resolved_argc"] = next_instruction["imm"]

	return instructions


def decode_pkc(path: str) -> dict:
	container = read_pkc(path)
	return {
		"format": "pkc-json-1",
		"source_basename": container["basename"],
		"header": {
			"magic_hex": container["magic_hex"],
			"flags": container["flags"],
			"reserved": container["reserved"],
		},
		"physical_layout": {
			"declared_code_size": container["code_size"],
			"code_continues_in_trailing": False,
		},
		"code_section": {
			"logical_code_size": len(container["code_raw"]),
			"instructions": decode_instruction_stream(container["code_raw"]),
		},
		"data_section": {
			"encoding": "raw",
			"raw_hex": hex_bytes(container["data_raw"]),
			"count": container["data_count"],
		},
		"string_section": {
			"encoding": "raw",
			"raw_hex": hex_bytes(container["string_raw"]),
			"count": container["string_count"],
		},
		"trailing_raw_hex": hex_bytes(container["trailing_raw"]),
	}


def encode_instruction(instruction: dict) -> bytes:
	opcode = int(instruction["opcode"])
	imm = int(instruction["imm"])
	out = bytearray()
	out.append(opcode)
	out.extend(imm.to_bytes(3, "big"))
	payload_hex = instruction.get("payload_hex", "")
	if payload_hex:
		out.extend(bytes.fromhex(payload_hex))
	return bytes(out)


def encode_pkc_document(document: dict) -> bytes:
	magic = bytes.fromhex(document["header"]["magic_hex"])
	flags = int(document["header"]["flags"])
	reserved = int(document["header"].get("reserved", 0))

	code_bytes = bytearray()
	for instruction in document["code_section"]["instructions"]:
		code_bytes.extend(encode_instruction(instruction))
	code_bytes = bytes(code_bytes)

	data_bytes = bytes.fromhex(document["data_section"].get("raw_hex", ""))
	data_count = int(document["data_section"].get("count", 0))
	string_bytes = bytes.fromhex(document["string_section"].get("raw_hex", ""))
	string_count = int(document["string_section"].get("count", 0))
	trailing_bytes = bytes.fromhex(document.get("trailing_raw_hex", ""))

	header = struct.pack(">7I", flags, len(code_bytes), len(data_bytes), data_count, len(string_bytes), string_count, reserved)
	return magic + header + code_bytes + data_bytes + string_bytes + trailing_bytes


def is_push_literal(instruction: dict) -> bool:
	return instruction.get("push_type") in ("int", "float")


def resolve_call_argc(instruction: dict) -> Optional[int]:
	argc = instruction.get("resolved_argc")
	if argc is not None:
		return int(argc)
	argc = instruction.get("declared_argc")
	if argc == VARARGS_SENTINEL:
		return None
	return argc


def match_function_call(instructions: List[dict], index: int, function_name: str, argc: Optional[int]) -> Optional[dict]:
	if not (0 <= index < len(instructions)):
		return None
	instruction = instructions[index]
	if instruction["opcode"] not in (PKCOpCode.call_ext_0.value, PKCOpCode.call_ext_1.value):
		return None
	if instruction.get("function_name") != function_name:
		return None
	if argc is not None and resolve_call_argc(instruction) != argc:
		return None
	return instruction


def make_pc_hex(pc_units: int) -> str:
	return f"0x{int(pc_units):04X}"


def int_enum_to_symbol(value: int, mapping: Dict[int, str], prefix: str):
	if value in mapping:
		return mapping[value]
	if value >= 0:
		return f"{prefix}_{value:04d}"
	return value


def symbol_or_int_to_value(value, mapping: Dict[str, int], fallback_re: re.Pattern) -> int:
	if isinstance(value, int):
		return value
	if isinstance(value, str):
		text = value.strip()
		if text in mapping:
			return mapping[text]
		match = fallback_re.fullmatch(text)
		if match is not None:
			return int(match.group(1), 10)
	try:
		return int(value)
	except Exception as exc:
		raise ValueError(f"Invalid enum-compatible value: {value!r}") from exc


def normalize_number(value):
	if isinstance(value, bool):
		return int(value)
	if isinstance(value, int):
		return value
	if isinstance(value, float):
		return value
	if isinstance(value, str):
		text = value.strip()
		if not text:
			raise ValueError("Empty string is not a valid numeric value")
		try:
			if any(ch in text for ch in ".eE"):
				return float(text)
			return int(text, 10)
		except ValueError as exc:
			raise ValueError(f"Invalid numeric text: {value!r}") from exc
	raise ValueError(f"Unsupported numeric type: {type(value).__name__}")


def assign_push_value(instruction: dict, value, preferred_type: str) -> None:
	if preferred_type == "int":
		integer_value = int(normalize_number(value))
		if not -0x800000 <= integer_value <= 0x7FFFFF:
			raise ValueError(f"Integer value out of signed 24-bit range: {integer_value}")
		instruction["opcode"] = PKCOpCode.push_int.value
		instruction["imm"] = integer_value & 0xFFFFFF
		instruction["payload_hex"] = ""
		instruction["length_bytes"] = 4
		instruction["length_units"] = 1
		instruction["push_type"] = "int"
		instruction["push_value"] = integer_value
		return

	float_value = float(normalize_number(value))
	instruction["opcode"] = PKCOpCode.push_s_f.value
	instruction["imm"] = 0
	instruction["payload_hex"] = struct.pack(">f", float_value).hex().upper()
	instruction["length_bytes"] = 8
	instruction["length_units"] = 2
	instruction["push_type"] = "float"
	instruction["push_value"] = float_value


def match_stage_target_pattern(instructions: List[dict], index: int) -> Optional[dict]:
	if index + 4 >= len(instructions):
		return None
	pushes = instructions[index:index + 3]
	call_instruction = instructions[index + 3]
	pop_instruction = instructions[index + 4]
	if not all(is_push_literal(instruction) and instruction["push_type"] == "int" for instruction in pushes):
		return None
	if match_function_call(instructions, index + 3, "SetStageDescTarget", 3) is None:
		return None
	if pop_instruction["opcode"] != PKCOpCode.pop.value:
		return None
	return {
		"difficulty": pushes[0]["push_value"],
		"rank": pushes[1]["push_value"],
		"stage_type": pushes[2]["push_value"],
		"push_indexes": [instruction["index"] for instruction in pushes],
		"start_index": index,
		"end_index": index + 4,
		"target_pc": call_instruction["pc_units"],
	}


def match_stage_params_pattern(instructions: List[dict], index: int) -> Optional[dict]:
	if index + 10 >= len(instructions):
		return None
	pushes = instructions[index:index + 8]
	call_instruction = instructions[index + 8]
	nop_instruction = instructions[index + 9]
	pop_instruction = instructions[index + 10]
	if not all(is_push_literal(instruction) for instruction in pushes):
		return None
	if match_function_call(instructions, index + 8, "SetStageDescParams", 8) is None:
		return None
	if nop_instruction["opcode"] != PKCOpCode.nop_0.value or pop_instruction["opcode"] != PKCOpCode.pop.value:
		return None
	return {
		"values": [instruction["push_value"] for instruction in pushes],
		"push_indexes": [instruction["index"] for instruction in pushes],
		"push_types": [instruction["push_type"] for instruction in pushes],
		"start_index": index,
		"end_index": index + 10,
		"params_pc": call_instruction["pc_units"],
	}


def parse_stage_desc_groups(document: dict) -> List[dict]:
	instructions = document["code_section"]["instructions"]
	groups = []
	index = 0
	group_index = 0

	while index < len(instructions):
		targets = []
		group_start = index
		while True:
			target_match = match_stage_target_pattern(instructions, index)
			if target_match is None:
				break
			targets.append(target_match)
			index = target_match["end_index"] + 1

		if targets:
			params_match = match_stage_params_pattern(instructions, index)
			if params_match is not None:
				groups.append({
					"group_index": group_index,
					"targets": targets,
					"params": params_match,
					"start_index": targets[0]["start_index"],
					"end_index": params_match["end_index"],
				})
				group_index += 1
				index = params_match["end_index"] + 1
				continue
			index = group_start + 1
			continue

		index += 1

	return groups


def match_map_target_pattern(instructions: List[dict], index: int) -> Optional[dict]:
	if index + 2 >= len(instructions):
		return None
	push_instruction = instructions[index]
	call_instruction = instructions[index + 1]
	pop_instruction = instructions[index + 2]
	if not (is_push_literal(push_instruction) and push_instruction["push_type"] == "int"):
		return None
	if match_function_call(instructions, index + 1, "SetMapDescTarget", 1) is None:
		return None
	if pop_instruction["opcode"] != PKCOpCode.pop.value:
		return None
	return {
		"map_desc_id": push_instruction["push_value"],
		"push_index": push_instruction["index"],
		"push_type": push_instruction["push_type"],
		"start_index": index,
		"end_index": index + 2,
		"target_pc": call_instruction["pc_units"],
	}


def match_map_params_pattern(instructions: List[dict], index: int) -> Optional[dict]:
	if index + 6 >= len(instructions):
		return None
	pushes = instructions[index:index + 4]
	call_instruction = instructions[index + 4]
	nop_instruction = instructions[index + 5]
	pop_instruction = instructions[index + 6]
	if not all(is_push_literal(instruction) for instruction in pushes):
		return None
	if match_function_call(instructions, index + 4, "SetMapDescParams", 4) is None:
		return None
	if nop_instruction["opcode"] != PKCOpCode.nop_0.value or pop_instruction["opcode"] != PKCOpCode.pop.value:
		return None
	return {
		"values": [instruction["push_value"] for instruction in pushes],
		"push_indexes": [instruction["index"] for instruction in pushes],
		"push_types": [instruction["push_type"] for instruction in pushes],
		"start_index": index,
		"end_index": index + 6,
		"params_pc": call_instruction["pc_units"],
	}


def match_map_init_pattern(instructions: List[dict], index: int) -> Optional[dict]:
	if index + 3 >= len(instructions):
		return None
	pushes = instructions[index:index + 2]
	call_instruction = instructions[index + 2]
	pop_instruction = instructions[index + 3]
	if not all(is_push_literal(instruction) and instruction["push_type"] == "int" for instruction in pushes):
		return None
	if match_function_call(instructions, index + 2, "InitMapDescEnemies", 2) is None:
		return None
	if pop_instruction["opcode"] != PKCOpCode.pop.value:
		return None
	return {
		"values": [instruction["push_value"] for instruction in pushes],
		"push_indexes": [instruction["index"] for instruction in pushes],
		"push_types": [instruction["push_type"] for instruction in pushes],
		"start_index": index,
		"end_index": index + 3,
		"init_pc": call_instruction["pc_units"],
	}


def match_map_enemy_row_pattern(instructions: List[dict], index: int) -> Optional[dict]:
	if index + 10 >= len(instructions):
		return None
	pushes = instructions[index:index + 8]
	call_instruction = instructions[index + 8]
	nop_instruction = instructions[index + 9]
	pop_instruction = instructions[index + 10]
	if not all(is_push_literal(instruction) for instruction in pushes):
		return None
	if match_function_call(instructions, index + 8, "SetMapDescEnemyRow", 8) is None:
		return None
	if nop_instruction["opcode"] != PKCOpCode.nop_0.value or pop_instruction["opcode"] != PKCOpCode.pop.value:
		return None
	return {
		"values": [instruction["push_value"] for instruction in pushes],
		"push_indexes": [instruction["index"] for instruction in pushes],
		"push_types": [instruction["push_type"] for instruction in pushes],
		"start_index": index,
		"end_index": index + 10,
		"row_pc": call_instruction["pc_units"],
	}


def parse_map_desc_entries(document: dict) -> List[dict]:
	instructions = document["code_section"]["instructions"]
	entries = []
	index = 0
	entry_index = 0

	while index < len(instructions):
		target_match = match_map_target_pattern(instructions, index)
		if target_match is None:
			index += 1
			continue

		params_match = match_map_params_pattern(instructions, target_match["end_index"] + 1)
		init_match = match_map_init_pattern(instructions, params_match["end_index"] + 1) if params_match is not None else None
		if params_match is None or init_match is None:
			index += 1
			continue

		rows = []
		cursor = init_match["end_index"] + 1
		while True:
			row_match = match_map_enemy_row_pattern(instructions, cursor)
			if row_match is None:
				break
			rows.append(row_match)
			cursor = row_match["end_index"] + 1

		entries.append({
			"entry_index": entry_index,
			"target": target_match,
			"params": params_match,
			"init": init_match,
			"rows": rows,
			"start_index": target_match["start_index"],
			"end_index": rows[-1]["end_index"] if rows else init_match["end_index"],
		})
		entry_index += 1
		index = cursor

	return entries


def export_species_field(field_name: str, value):
	if isinstance(value, int):
		if field_name in NULLABLE_SPECIES_FIELD_NAMES and value == 0:
			return None
		return int_enum_to_symbol(value, SPECIES_ID_TO_SYMBOL, "SPECIES")
	return value


def import_species_field(field_name: str, value):
	if value is None and field_name in NULLABLE_SPECIES_FIELD_NAMES:
		return 0
	return symbol_or_int_to_value(value, SPECIES_SYMBOL_TO_ID, SPECIES_ENUM_RE)


def split_edit_entries(edit_document: dict):
	entries = edit_document.get("entries")
	if isinstance(entries, list):
		return entries

	stage_groups = edit_document.get("stage_desc_groups")
	map_entries = edit_document.get("map_desc_entries")
	if isinstance(stage_groups, list) and isinstance(map_entries, list):
		map_entries_by_id: Dict[int, List[dict]] = {}
		for map_entry in map_entries:
			if not isinstance(map_entry, dict):
				raise ValueError("map_desc_entries must contain only objects")
			map_desc_id = normalize_number(map_entry.get("map_desc_id"))
			map_entries_by_id.setdefault(map_desc_id, []).append(map_entry)
		merged_entries = []
		for stage_group in stage_groups:
			if not isinstance(stage_group, dict):
				raise ValueError("stage_desc_groups must contain only objects")
			fields = stage_group.get("fields")
			map_desc_id = normalize_number(fields.get("map_desc_id")) if isinstance(fields, dict) else None
			merged_entry = dict(stage_group)
			merged_entry.setdefault("map_desc_entries", map_entries_by_id.get(map_desc_id, []))
			merged_entries.append(merged_entry)
		return merged_entries

	raise ValueError("JSON is missing entries list")


def build_stage_group_fields(values: List[float]) -> dict:
	fields = {}
	for index, name in enumerate(STAGE_PARAM_NAMES):
		value = values[index]
		if name in SPECIES_FIELD_NAMES:
			fields[name] = export_species_field(name, value)
		else:
			fields[name] = value
	return fields


def build_map_params_fields(values: List[float]) -> dict:
	return {name: values[index] for index, name in enumerate(MAP_PARAM_NAMES)}


def build_map_init_fields(values: List[float]) -> dict:
	return {name: values[index] for index, name in enumerate(MAP_INIT_NAMES)}


def build_map_enemy_row(values: List[float]) -> dict:
	row = {
		"row_index": values[0],
	}
	for index, name in enumerate(MAP_ROW_FIELD_NAMES, start=1):
		value = values[index]
		if name in SPECIES_FIELD_NAMES:
			row[name] = export_species_field(name, value)
		else:
			row[name] = value
	return row



def build_map_desc_payload(entry: dict, instructions: List[dict]) -> dict:
	return {
		"entry_index": entry["entry_index"],
		"map_desc_id": entry["target"]["map_desc_id"],
		"pc_range": {
			"start": make_pc_hex(instructions[entry["start_index"]]["pc_units"]),
			"init_rows": make_pc_hex(entry["init"]["init_pc"]),
			"end": make_pc_hex(instructions[entry["end_index"]]["pc_units"]),
		},
		"map_params": build_map_params_fields(entry["params"]["values"]),
		"enemy_table_header": build_map_init_fields(entry["init"]["values"]),
		"enemy_rows": [build_map_enemy_row(row["values"]) for row in entry["rows"]],
	}


def build_export_document(pkc_path: str) -> dict:
	document = decode_pkc(pkc_path)
	stage_groups = parse_stage_desc_groups(document)
	map_entries = parse_map_desc_entries(document)
	instructions = document["code_section"]["instructions"]
	map_entries_by_id: Dict[int, List[dict]] = {}
	for entry in map_entries:
		map_entries_by_id.setdefault(entry["target"]["map_desc_id"], []).append(entry)

	entries = []
	for group in stage_groups:
		targets = []
		for target in group["targets"]:
			targets.append({
				"difficulty": int_enum_to_symbol(target["difficulty"], DIFFICULTY_ID_TO_SYMBOL, "DIFFICULTY"),
				"rank": int_enum_to_symbol(target["rank"], RANK_ID_TO_SYMBOL, "RANK"),
				"stage_type": int_enum_to_symbol(target["stage_type"], STAGE_ID_TO_SYMBOL, "MAP"),
			})
		fields = build_stage_group_fields(group["params"]["values"])
		map_desc_id = fields["map_desc_id"]
		related_map_entries = [build_map_desc_payload(entry, instructions) for entry in map_entries_by_id.get(map_desc_id, [])]
		entries.append({
			"group_index": group["group_index"],
			"target_count": len(targets),
			"targets": targets,
			"pc_range": {
				"start": make_pc_hex(instructions[group["start_index"]]["pc_units"]),
				"params": make_pc_hex(group["params"]["params_pc"]),
				"end": make_pc_hex(instructions[group["end_index"]]["pc_units"]),
			},
			"fields": fields,
			"map_desc_entries": related_map_entries,
		})

	return {
		"format": FORMAT_VERSION,
		"source_pkc": os.path.basename(pkc_path),
		"notes": {
			"editing": [
				"StageDescData exports as one entries list keyed by stage_desc group.",
				"Each entry contains its targets, stage fields, and all map_desc_entries that share its map_desc_id.",
				"targets use DifficultyEnum, RankEnum, and StageEnum symbols from pyPKCEnumLib.py.",
				"species fields use SpeciesEnum symbols from pyPKCEnumLib.py.",
				"species_secondary and species_tertiary export as null when empty.",
				"Multiple stage entries can duplicate the same map_desc_entries when they reference the same map_desc_id.",
				"If duplicated map_desc_entries are edited inconsistently across entries that share a map_desc_id, import will fail.",
				"Adding or removing stage entries, map_desc_entries, or enemy_rows is not supported by this importer.",
			],
		},
		"entries": entries,
	}


def apply_stage_group_json(document: dict, edit_group: dict, original_group: dict) -> None:
	instructions = document["code_section"]["instructions"]
	targets = edit_group.get("targets")
	if not isinstance(targets, list):
		raise ValueError(f"stage_desc_groups[{original_group['group_index']}].targets must be a list")
	if len(targets) != len(original_group["targets"]):
		raise ValueError(f"stage_desc_groups[{original_group['group_index']}] target count changed from {len(original_group['targets'])} to {len(targets)}")

	for target_index, (target_json, target_original) in enumerate(zip(targets, original_group["targets"])):
		if not isinstance(target_json, dict):
			raise ValueError(f"stage_desc_groups[{original_group['group_index']}].targets[{target_index}] must be an object")
		target_values = [
			symbol_or_int_to_value(target_json.get("difficulty"), DIFFICULTY_SYMBOL_TO_ID, DIFFICULTY_ENUM_RE),
			symbol_or_int_to_value(target_json.get("rank"), RANK_SYMBOL_TO_ID, RANK_ENUM_RE),
			symbol_or_int_to_value(target_json.get("stage_type"), STAGE_SYMBOL_TO_ID, STAGE_ENUM_RE),
		]
		for value, instruction_index in zip(target_values, target_original["push_indexes"]):
			assign_push_value(instructions[instruction_index], value, "int")

	fields = edit_group.get("fields")
	if not isinstance(fields, dict):
		raise ValueError(f"stage_desc_groups[{original_group['group_index']}].fields must be an object")

	for field_index, field_name in enumerate(STAGE_PARAM_NAMES):
		if field_name not in fields:
			raise ValueError(f"stage_desc_groups[{original_group['group_index']}].fields is missing {field_name!r}")
		value = fields[field_name]
		if field_name in SPECIES_FIELD_NAMES:
			value = import_species_field(field_name, value)
		else:
			value = normalize_number(value)
		assign_push_value(instructions[original_group["params"]["push_indexes"][field_index]], value, original_group["params"]["push_types"][field_index])


def apply_map_entry_json(document: dict, edit_entry: dict, original_entry: dict) -> None:
	instructions = document["code_section"]["instructions"]

	map_desc_id = normalize_number(edit_entry.get("map_desc_id"))
	if not isinstance(map_desc_id, int):
		raise ValueError(f"map_desc_entries[{original_entry['entry_index']}].map_desc_id must be an integer")
	assign_push_value(instructions[original_entry["target"]["push_index"]], map_desc_id, original_entry["target"]["push_type"])

	map_params = edit_entry.get("map_params")
	if not isinstance(map_params, dict):
		raise ValueError(f"map_desc_entries[{original_entry['entry_index']}].map_params must be an object")
	for field_index, field_name in enumerate(MAP_PARAM_NAMES):
		if field_name not in map_params:
			raise ValueError(f"map_desc_entries[{original_entry['entry_index']}].map_params is missing {field_name!r}")
		value = normalize_number(map_params[field_name])
		assign_push_value(instructions[original_entry["params"]["push_indexes"][field_index]], value, original_entry["params"]["push_types"][field_index])

	enemy_table_header = edit_entry.get("enemy_table_header")
	if not isinstance(enemy_table_header, dict):
		raise ValueError(f"map_desc_entries[{original_entry['entry_index']}].enemy_table_header must be an object")
	for field_index, field_name in enumerate(MAP_INIT_NAMES):
		if field_name not in enemy_table_header:
			raise ValueError(f"map_desc_entries[{original_entry['entry_index']}].enemy_table_header is missing {field_name!r}")
		value = normalize_number(enemy_table_header[field_name])
		if not isinstance(value, int):
			raise ValueError(f"map_desc_entries[{original_entry['entry_index']}].enemy_table_header.{field_name} must be an integer")
		assign_push_value(instructions[original_entry["init"]["push_indexes"][field_index]], value, original_entry["init"]["push_types"][field_index])

	enemy_rows = edit_entry.get("enemy_rows")
	if not isinstance(enemy_rows, list):
		raise ValueError(f"map_desc_entries[{original_entry['entry_index']}].enemy_rows must be a list")
	if len(enemy_rows) != len(original_entry["rows"]):
		raise ValueError(f"map_desc_entries[{original_entry['entry_index']}] row count changed from {len(original_entry['rows'])} to {len(enemy_rows)}")


	for row_index, (row_json, row_original) in enumerate(zip(enemy_rows, original_entry["rows"])):
		if not isinstance(row_json, dict):
			raise ValueError(f"map_desc_entries[{original_entry['entry_index']}].enemy_rows[{row_index}] must be an object")
		row_values = []
		row_index_value = normalize_number(row_json.get("row_index"))
		if not isinstance(row_index_value, int):
			raise ValueError(f"map_desc_entries[{original_entry['entry_index']}].enemy_rows[{row_index}].row_index must be an integer")
		row_values.append(row_index_value)

		for field_name in MAP_ROW_FIELD_NAMES:
			if field_name not in row_json:
				raise ValueError(f"map_desc_entries[{original_entry['entry_index']}].enemy_rows[{row_index}] is missing {field_name!r}")
			value = row_json[field_name]
			if field_name in SPECIES_FIELD_NAMES:
				value = import_species_field(field_name, value)
			else:
				value = normalize_number(value)
			row_values.append(value)

		for value, instruction_index, preferred_type in zip(row_values, row_original["push_indexes"], row_original["push_types"]):
			assign_push_value(instructions[instruction_index], value, preferred_type)


def apply_json_to_document(document: dict, edit_document: dict) -> dict:
	if edit_document.get("format") != FORMAT_VERSION:
		raise ValueError(f"Unsupported JSON format: {edit_document.get('format')!r}")

	stage_groups = parse_stage_desc_groups(document)
	map_entries = parse_map_desc_entries(document)
	edit_entries = split_edit_entries(edit_document)

	if len(edit_entries) != len(stage_groups):
		raise ValueError(f"entries count changed from {len(stage_groups)} to {len(edit_entries)}")

	stage_groups_by_index = {group["group_index"]: group for group in stage_groups}
	map_entries_by_id: Dict[int, List[dict]] = {}
	for entry in map_entries:
		map_entries_by_id.setdefault(entry["target"]["map_desc_id"], []).append(entry)

	requested_map_edits: Dict[int, List[dict]] = {}

	for edit_entry in edit_entries:
		if not isinstance(edit_entry, dict):
			raise ValueError("entries must contain only objects")
		group_index = normalize_number(edit_entry.get("group_index"))
		if group_index not in stage_groups_by_index:
			raise ValueError(f"Unknown entries group_index: {group_index}")
		original_group = stage_groups_by_index[group_index]
		apply_stage_group_json(document, edit_entry, original_group)

		fields = edit_entry.get("fields")
		if not isinstance(fields, dict):
			raise ValueError(f"entries[{group_index}].fields must be an object")
		map_desc_id = normalize_number(fields.get("map_desc_id"))
		related_map_entries = edit_entry.get("map_desc_entries", [])
		if not isinstance(related_map_entries, list):
			raise ValueError(f"entries[{group_index}].map_desc_entries must be a list")
		if map_desc_id == 0 and not related_map_entries:
			continue
		original_bucket = map_entries_by_id.get(map_desc_id, [])
		if len(related_map_entries) != len(original_bucket):
			raise ValueError(f"entries[{group_index}].map_desc_entries count changed from {len(original_bucket)} to {len(related_map_entries)} for map_desc_id {map_desc_id}")
		previous = requested_map_edits.get(map_desc_id)
		if previous is None:
			requested_map_edits[map_desc_id] = related_map_entries
		elif previous != related_map_entries:
			raise ValueError(f"Inconsistent edits for shared map_desc_id {map_desc_id}; entries that share a map_desc_id must keep identical map_desc_entries")

	for map_desc_id, related_map_entries in requested_map_edits.items():
		original_bucket = map_entries_by_id.get(map_desc_id, [])
		for edit_map_entry, original_map_entry in zip(related_map_entries, original_bucket):
			apply_map_entry_json(document, edit_map_entry, original_map_entry)

	return document


def default_rebuilt_output_path(original_path: str) -> str:
	root, ext = os.path.splitext(original_path)
	if not ext:
		return root + ".rebuilt"
	return root + ".rebuilt" + ext


def write_json(path: str, payload: dict) -> None:
	with open(path, "w", encoding="utf-8") as outfile:
		json.dump(payload, outfile, ensure_ascii=False, indent="\t")
		outfile.write("\n")


def command_export(args) -> None:
	export_document = build_export_document(args.input_pkc)
	output_path = args.output_json if args.output_json else os.path.splitext(args.input_pkc)[0] + ".json"
	write_json(output_path, export_document)
	print(f"Wrote {output_path}")


def command_import(args) -> None:
	with open(args.input_json, "r", encoding="utf-8") as infile:
		edit_document = json.load(infile)

	document = decode_pkc(args.original_pkc)
	updated_document = apply_json_to_document(document, edit_document)
	output_path = args.output_pkc if args.output_pkc else default_rebuilt_output_path(args.original_pkc)
	with open(output_path, "wb") as outfile:
		outfile.write(encode_pkc_document(updated_document))
	print(f"Wrote {output_path}")


def command_verify(args) -> None:
	export_document = build_export_document(args.input_pkc)
	document = decode_pkc(args.input_pkc)
	updated_document = apply_json_to_document(document, export_document)
	rebuilt_raw = encode_pkc_document(updated_document)
	original_raw = open(args.input_pkc, "rb").read()
	stage_group_count = len(export_document["entries"])
	map_entry_count = sum(len(entry.get("map_desc_entries", [])) for entry in export_document["entries"])
	print("Match: YES" if rebuilt_raw == original_raw else "Match: NO")
	print(f"Stage groups: {stage_group_count}")
	print(f"Related map entries: {map_entry_count}")


def build_arg_parser() -> argparse.ArgumentParser:
	parser = argparse.ArgumentParser(description="Export/import StageDescData PKC as editable JSON using shared enums from pyPKCEnumLib.py.")
	subparsers = parser.add_subparsers(dest="command", required=True)

	export_parser = subparsers.add_parser("export", help="Export StageDescData PKC to editable JSON")
	export_parser.add_argument("input_pkc", help="Path to StageDescData.pkc")
	export_parser.add_argument("output_json", nargs="?", help="Optional output JSON path")
	export_parser.set_defaults(func=command_export)

	import_parser = subparsers.add_parser("import", help="Import editable JSON back into the original StageDescData PKC")
	import_parser.add_argument("input_json", help="Path to edited JSON")
	import_parser.add_argument("original_pkc", help="Path to original StageDescData.pkc")
	import_parser.add_argument("output_pkc", nargs="?", help="Optional output PKC path. Defaults to <original>.rebuilt.pkc")
	import_parser.set_defaults(func=command_import)

	verify_parser = subparsers.add_parser("verify", help="Export and re-import without changes, then compare bytes")
	verify_parser.add_argument("input_pkc", help="Path to StageDescData.pkc")
	verify_parser.set_defaults(func=command_verify)

	return parser


def main() -> None:
	parser = build_arg_parser()
	args = parser.parse_args()
	args.func(args)


if __name__ == "__main__":
	main()
