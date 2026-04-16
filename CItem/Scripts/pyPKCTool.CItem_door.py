#!/usr/bin/env python3
import argparse
import copy
import json
import os
import sys

try:
	from pyPKCToolBase import decode_pkc, encode_pkc_document
except ImportError as exc:
	print(f"Failed to import pyPKCToolBase.py: {exc}", file=sys.stderr)
	sys.exit(1)


TOOL_DESCRIPTION = "Edit CItem_door.pkc via semantic JSON."
FORMAT_VERSION = "citem-door-semantic-json-1"

EVENT_SCRIPT_PCS = [
	0x0AE2, 0x0AE8, 0x0AEE, 0x0AF4, 0x0AFA, 0x0B00, 0x0B06, 0x0B0C,
	0x0B12, 0x0B18, 0x0B1E, 0x0B24, 0x0B2A, 0x0B30, 0x0B36, 0x0B3C,
	0x0B58,
]
REWARD_ITEM_SCRIPT_PC = 0x0EAD
DUMMY_IDLE_SCRIPT_PC = 0x1054
DUMMY_DOOR_SCRIPT_PC = 0x10C5
DOOR_MODEL_BRRES_PC = 0x105A
PROLOGUE_MOTION_PC = 0x1064
CLOSED_GROUND_ATTR_PC = 0x1072
CLOSE_LOOP_MOTION_PC = 0x1076
OPEN_LOOP_MOTION_PC = 0x107B
DUMMY_DOOR_OFFSET_ZX_PC = 0x10C9

FLAG_PC_MAP = {
	"prologue_play_flag": 0x105F,
	"interaction_flag": 0x0BD1,
}

MESSAGE_PC_MAP = {
	"dynamic_category_base_high": 0x0FBC,
	"dynamic_category_base_low": 0x0FA6,
	"difficulty_rank_labels": [0x0FA6, 0x0FAB, 0x0FB0, 0x0FBC, 0x0FC1, 0x0FC6, 0x0FCB, 0x0FD0],
}

WAZA_DESC_FIELD_PCS = [0x0FF4, 0x0FF8, 0x0FFC, 0x1000, 0x1004]
STAGE_VALUE_TABLE_A_PCS = [0x0F4D, 0x0F4F, 0x0F51, 0x0F53, 0x0F59, 0x0F5B, 0x0F5D, 0x0F5F, 0x0F64]
STAGE_VALUE_TABLE_B_PCS = [0x0F7C, 0x0F7E, 0x0F80, 0x0F82, 0x0F88, 0x0F8A, 0x0F8C, 0x0F8E, 0x0F93]
DIFFICULTY_WEIGHT_TRIPLE_PCS = [
	[0x0ED4, 0x0ED6, 0x0ED8],
	[0x0EDB, 0x0EDD, 0x0EDF],
	[0x0EE2, 0x0EE4, 0x0EE6],
	[0x0EE9, 0x0EEB, 0x0EED],
]
GRADE_WEIGHT_TRIPLE_PCS = [
	[0x0EF5, 0x0EF7, 0x0EF9],
	[0x0EFC, 0x0EFE, 0x0F00],
	[0x0F03, 0x0F05, 0x0F07],
	[0x0F0A, 0x0F0C, 0x0F0E],
]


def dump_json(path, document):
	with open(path, "w", encoding="utf-8", newline="\n") as outfile:
		outfile.write(json.dumps(document, ensure_ascii=False, indent="\t"))
		outfile.write("\n")


def load_json(path):
	with open(path, "r", encoding="utf-8") as infile:
		return json.load(infile)


def basename_without_ext(path):
	return os.path.splitext(os.path.basename(path))[0]


def default_json_output(input_path):
	return basename_without_ext(input_path) + ".json"


def default_pkc_output(original_pkc_path):
	base, ext = os.path.splitext(original_pkc_path)
	return base + ".rebuilt" + ext


def signed24_to_u24(value):
	integer = int(value)
	if not -(1 << 23) <= integer < (1 << 23):
		raise ValueError(f"Value out of signed 24-bit range: {integer}")
	return integer & 0xFFFFFF


def build_pc_maps(document):
	pc_to_instruction = {}
	pc_to_string_index = {}
	for instruction in document["code_section"]["instructions"]:
		pc = int(instruction["pc_units"])
		pc_to_instruction[pc] = instruction
		if instruction["opname"] == "push_str":
			decoded = instruction.get("decoded", {})
			pc_to_string_index[pc] = int(decoded["index"])
	return pc_to_instruction, pc_to_string_index


def expect_instruction(pc_to_instruction, pc, opname):
	instruction = pc_to_instruction.get(pc)
	if instruction is None:
		raise ValueError(f"Expected instruction at PC 0x{pc:04X}, but none was found")
	if instruction["opname"] != opname:
		raise ValueError(f"Expected {opname} at PC 0x{pc:04X}, found {instruction['opname']}")
	return instruction


def read_push_int(pc_to_instruction, pc):
	instruction = expect_instruction(pc_to_instruction, pc, "push_int")
	return int(instruction.get("decoded", {}).get("value_signed"))


def write_push_int(pc_to_instruction, pc, value):
	instruction = expect_instruction(pc_to_instruction, pc, "push_int")
	instruction["imm"] = signed24_to_u24(value)
	instruction["imm_hex"] = f"0x{instruction['imm']:06X}"
	instruction["decoded"] = {"type": "int24", "value_signed": int(value), "value_unsigned": instruction["imm"]}


def read_push_float(pc_to_instruction, pc, negate=False):
	instruction = expect_instruction(pc_to_instruction, pc, "push_s_f")
	value = float(instruction.get("decoded", {}).get("value"))
	return -value if negate else value


def write_push_float(pc_to_instruction, pc, value, negate=False):
	instruction = expect_instruction(pc_to_instruction, pc, "push_s_f")
	float_value = float(-value if negate else value)
	instruction["decoded"] = {"type": "float", "value": float_value, "source_text": repr(float_value), "bit_pattern_hex": None}


def read_push_str(string_entries, pc_to_string_index, pc):
	if pc not in pc_to_string_index:
		raise ValueError(f"Expected push_str at PC 0x{pc:04X}, but none was found")
	index = pc_to_string_index[pc]
	return string_entries[index], index


def write_push_str_group(string_entries, pc_to_string_index, pcs, values):
	index_to_value = {}
	for pc, value in zip(pcs, values):
		if pc not in pc_to_string_index:
			
			raise ValueError(f"Expected push_str at PC 0x{pc:04X}, but none was found")
		index = pc_to_string_index[pc]
		if index in index_to_value and index_to_value[index] != value:
			raise ValueError(f"Conflicting values for shared string index {index}: {index_to_value[index]!r} vs {value!r}")
		index_to_value[index] = value
	for index, value in index_to_value.items():
		string_entries[index] = str(value)


def read_string_list(string_entries, pc_to_string_index, pcs):
	return [read_push_str(string_entries, pc_to_string_index, pc)[0] for pc in pcs]


def read_int_list(pc_to_instruction, pcs):
	return [read_push_int(pc_to_instruction, pc) for pc in pcs]


def write_int_list(pc_to_instruction, pcs, values, label):
	if len(values) != len(pcs):
		raise ValueError(f"{label} must contain {len(pcs)} values, got {len(values)}")
	for pc, value in zip(pcs, values):
		write_push_int(pc_to_instruction, pc, value)


def export_semantic(document):
	pc_to_instruction, pc_to_string_index = build_pc_maps(document)
	string_entries = document["string_section"]["entries"]
	return {
		"format": FORMAT_VERSION,
		"source_basename": document.get("source_basename"),
		"embedded_scripts": {
			"event_sequence": read_string_list(string_entries, pc_to_string_index, EVENT_SCRIPT_PCS),
			"reward_item_script": read_push_str(string_entries, pc_to_string_index, REWARD_ITEM_SCRIPT_PC)[0],
			"dummy_idle_script": read_push_str(string_entries, pc_to_string_index, DUMMY_IDLE_SCRIPT_PC)[0],
			"dummy_door_script": read_push_str(string_entries, pc_to_string_index, DUMMY_DOOR_SCRIPT_PC)[0],
		},
		"door_visuals": {
			"door_model_brres": read_push_str(string_entries, pc_to_string_index, DOOR_MODEL_BRRES_PC)[0],
			"prologue_motion": read_push_str(string_entries, pc_to_string_index, PROLOGUE_MOTION_PC)[0],
			"closed_ground_attr": read_push_str(string_entries, pc_to_string_index, CLOSED_GROUND_ATTR_PC)[0],
			"close_loop_motion": read_push_str(string_entries, pc_to_string_index, CLOSE_LOOP_MOTION_PC)[0],
			"open_loop_motion": read_push_str(string_entries, pc_to_string_index, OPEN_LOOP_MOTION_PC)[0],
			"dummy_door_offset_zx": read_push_float(pc_to_instruction, DUMMY_DOOR_OFFSET_ZX_PC, negate=True),
		},
		"flags": {
			"prologue_play_flag": read_push_int(pc_to_instruction, FLAG_PC_MAP["prologue_play_flag"]),
			"interaction_flag": read_push_int(pc_to_instruction, FLAG_PC_MAP["interaction_flag"]),
		},
		"messages": {
			"dynamic_category_base_high": read_push_int(pc_to_instruction, MESSAGE_PC_MAP["dynamic_category_base_high"]),
			"dynamic_category_base_low": read_push_int(pc_to_instruction, MESSAGE_PC_MAP["dynamic_category_base_low"]),
			"difficulty_rank_labels": read_int_list(pc_to_instruction, MESSAGE_PC_MAP["difficulty_rank_labels"]),
		},
		"waza_desc_fields": read_int_list(pc_to_instruction, WAZA_DESC_FIELD_PCS),
		"tables": {
			"stage_value_table_a": read_int_list(pc_to_instruction, STAGE_VALUE_TABLE_A_PCS),
			"stage_value_table_b": read_int_list(pc_to_instruction, STAGE_VALUE_TABLE_B_PCS),
			"difficulty_weight_triples": [read_int_list(pc_to_instruction, group) for group in DIFFICULTY_WEIGHT_TRIPLE_PCS],
			"grade_weight_triples": [read_int_list(pc_to_instruction, group) for group in GRADE_WEIGHT_TRIPLE_PCS],
		},
	}


def apply_semantic(document, semantic):
	if semantic.get("format") != FORMAT_VERSION:
		raise ValueError(f"Unsupported JSON format: {semantic.get('format')!r}")
	pc_to_instruction, pc_to_string_index = build_pc_maps(document)
	string_entries = document["string_section"]["entries"]
	write_push_str_group(string_entries, pc_to_string_index, EVENT_SCRIPT_PCS, semantic["embedded_scripts"]["event_sequence"])
	write_push_str_group(string_entries, pc_to_string_index, [REWARD_ITEM_SCRIPT_PC], [semantic["embedded_scripts"]["reward_item_script"]])
	write_push_str_group(string_entries, pc_to_string_index, [DUMMY_IDLE_SCRIPT_PC], [semantic["embedded_scripts"]["dummy_idle_script"]])
	write_push_str_group(string_entries, pc_to_string_index, [DUMMY_DOOR_SCRIPT_PC], [semantic["embedded_scripts"]["dummy_door_script"]])
	for key, pc in [("door_model_brres", DOOR_MODEL_BRRES_PC), ("prologue_motion", PROLOGUE_MOTION_PC), ("closed_ground_attr", CLOSED_GROUND_ATTR_PC), ("close_loop_motion", CLOSE_LOOP_MOTION_PC), ("open_loop_motion", OPEN_LOOP_MOTION_PC)]:
		write_push_str_group(string_entries, pc_to_string_index, [pc], [semantic["door_visuals"][key]])
	write_push_float(pc_to_instruction, DUMMY_DOOR_OFFSET_ZX_PC, semantic["door_visuals"]["dummy_door_offset_zx"], negate=True)
	write_push_int(pc_to_instruction, FLAG_PC_MAP["prologue_play_flag"], semantic["flags"]["prologue_play_flag"])
	write_push_int(pc_to_instruction, FLAG_PC_MAP["interaction_flag"], semantic["flags"]["interaction_flag"])
	write_push_int(pc_to_instruction, MESSAGE_PC_MAP["dynamic_category_base_high"], semantic["messages"]["dynamic_category_base_high"])
	write_push_int(pc_to_instruction, MESSAGE_PC_MAP["dynamic_category_base_low"], semantic["messages"]["dynamic_category_base_low"])
	write_int_list(pc_to_instruction, MESSAGE_PC_MAP["difficulty_rank_labels"], semantic["messages"]["difficulty_rank_labels"], "messages.difficulty_rank_labels")
	write_int_list(pc_to_instruction, WAZA_DESC_FIELD_PCS, semantic["waza_desc_fields"], "waza_desc_fields")
	write_int_list(pc_to_instruction, STAGE_VALUE_TABLE_A_PCS, semantic["tables"]["stage_value_table_a"], "tables.stage_value_table_a")
	write_int_list(pc_to_instruction, STAGE_VALUE_TABLE_B_PCS, semantic["tables"]["stage_value_table_b"], "tables.stage_value_table_b")
	if len(semantic["tables"]["difficulty_weight_triples"]) != len(DIFFICULTY_WEIGHT_TRIPLE_PCS):
		raise ValueError(f"tables.difficulty_weight_triples must contain {len(DIFFICULTY_WEIGHT_TRIPLE_PCS)} rows")
	if len(semantic["tables"]["grade_weight_triples"]) != len(GRADE_WEIGHT_TRIPLE_PCS):
		raise ValueError(f"tables.grade_weight_triples must contain {len(GRADE_WEIGHT_TRIPLE_PCS)} rows")
	for group_pcs, row in zip(DIFFICULTY_WEIGHT_TRIPLE_PCS, semantic["tables"]["difficulty_weight_triples"]):
		write_int_list(pc_to_instruction, group_pcs, row, "tables.difficulty_weight_triples row")
	for group_pcs, row in zip(GRADE_WEIGHT_TRIPLE_PCS, semantic["tables"]["grade_weight_triples"]):
		write_int_list(pc_to_instruction, group_pcs, row, "tables.grade_weight_triples row")
	return document


def command_export(args):
	document = decode_pkc(args.input)
	semantic = export_semantic(document)
	output = args.output or default_json_output(args.input)
	dump_json(output, semantic)
	print(f"Wrote {output}")


def command_import(args):
	semantic = load_json(args.input)
	document = decode_pkc(args.original_pkc)
	document = apply_semantic(document, semantic)
	output = args.output or default_pkc_output(args.original_pkc)
	with open(output, "wb") as outfile:
		outfile.write(encode_pkc_document(document))
	print(f"Wrote {output}")


def command_verify(args):
	document = decode_pkc(args.input)
	semantic = export_semantic(document)
	rebuilt_document = apply_semantic(copy.deepcopy(document), semantic)
	rebuilt_bytes = encode_pkc_document(rebuilt_document)
	with open(args.input, "rb") as infile:
		original_bytes = infile.read()
	if rebuilt_bytes != original_bytes:
		print("Roundtrip FAILED", file=sys.stderr)
		for index, (left, right) in enumerate(zip(original_bytes, rebuilt_bytes)):
			if left != right:
				print(f"First difference at 0x{index:X}: original=0x{left:02X}, rebuilt=0x{right:02X}", file=sys.stderr)
				break
		if len(original_bytes) != len(rebuilt_bytes):
			print(f"Length differs: original={len(original_bytes)} rebuilt={len(rebuilt_bytes)}", file=sys.stderr)
		raise SystemExit(1)
	print("Roundtrip OK")


def build_arg_parser():
	parser = argparse.ArgumentParser(description=TOOL_DESCRIPTION)
	subparsers = parser.add_subparsers(dest="command", required=True)
	export_parser = subparsers.add_parser("export", help="Export a PKC to editable JSON.")
	export_parser.add_argument("input", help="Input PKC file")
	export_parser.add_argument("output", nargs="?", help="Output JSON file")
	export_parser.set_defaults(func=command_export)
	import_parser = subparsers.add_parser("import", help="Rebuild a PKC from exported JSON.")
	import_parser.add_argument("input", help="Input JSON file")
	import_parser.add_argument("original_pkc", help="Original PKC file used for default rebuilt naming")
	import_parser.add_argument("output", nargs="?", help="Output PKC file")
	import_parser.set_defaults(func=command_import)
	verify_parser = subparsers.add_parser("verify", help="Decode and immediately rebuild a PKC, then compare bytes.")
	verify_parser.add_argument("input", help="Input PKC file")
	verify_parser.set_defaults(func=command_verify)
	return parser


def main():
	parser = build_arg_parser()
	args = parser.parse_args()
	args.func(args)


if __name__ == "__main__":
	main()
