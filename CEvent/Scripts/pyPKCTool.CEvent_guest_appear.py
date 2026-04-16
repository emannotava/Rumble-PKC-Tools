#!/usr/bin/env python3
import argparse
import copy
import json
import os
import struct
import sys

try:
	from pyPKCToolBase import decode_pkc, encode_pkc_document
except ImportError as exc:
	print(f"Failed to import pyPKCToolBase.py: {exc}", file=sys.stderr)
	sys.exit(1)


TOOL_DESCRIPTION = 'Edit CEvent_guest_appear.pkc via semantic JSON.'
FORMAT_VERSION = 'cevent-guest-appear-semantic-json-1'
COMMON_NEWITEM_STRING_PCS = [916, 922, 928, 934, 940, 946, 952, 958, 964, 970, 976, 982, 988, 994, 1000, 1006, 1034]
MONEY_ITEM_SCRIPT_PC = 0x075F
RIVAL_STRING_GROUPS = {
	"event_data_name": [0x097A],
	"rival_motion": [0x0989, 0x09A0, 0x09C4, 0x09EE, 0x0A61, 0x0A9E],
	"layout_archive_path": [0x09F9],
	"layout_name": [0x09FA],
	"name_slot_main": [0x0A08],
	"name_slot_caption": [0x0A0D],
	"name_slot_shadow_1": [0x0A12],
	"name_slot_shadow_2": [0x0A17],
	"form_slot_main": [0x0A47],
	"form_slot_shadow_1": [0x0A4C],
	"form_slot_shadow_2": [0x0A51],
	"form_slot_hidden": [0x0A56],
	"rival_special_slot": [0x0A5C],
	"name_slot_alt_main": [0x0A66],
	"name_slot_alt_caption": [0x0A6B],
	"name_slot_alt_shadow_1": [0x0A70],
	"name_slot_alt_shadow_2": [0x0A75],
	"layout_state_begin": [0x0A7A],
	"layout_state_normal": [0x0A7F],
	"layout_state_end": [0x0A93],
}
RIVAL_INT_PCS = {
	"disappear_models_count": 0x0971,
	"disappear_models_frames": 0x0972,
	"show_minimap_before": 0x0977,
	"fade_out_frames": 0x0992,
	"intro_effect_species": 0x09AF,
	"intro_effect_normal_id": 0x09B2,
	"intro_effect_special_id": 0x09B4,
	"intro_effect_dir_deg": 0x09B8,
	"replay_wait_frames": 0x09BE,
	"appear_motion_id": 0x09D4,
	"request_motion_id": 0x09D8,
	"voice_delay_frames": 0x09E6,
	"voice_hold_frames": 0x09E7,
	"layout_name_mid": 0x0A04,
	"form_name_mid_base_0": 0x0A23,
	"form_name_mid_base_1": 0x0A28,
	"form_name_mid_base_2": 0x0A2D,
	"form_name_mid_base_3": 0x0A32,
	"form_name_mid_base_4": 0x0A37,
	"layout_state_begin_wait": 0x0A86,
	"layout_state_end_dir_deg": 0x0A87,
	"layout_state_end_hold": 0x0A89,
	"layout_state_end_flag": 0x0A8D,
	"layout_state_end_extra_wait": 0x0A8F,
	"show_minimap_after": 0x0AB6,
}
RIVAL_FLOAT_PCS = {
	"camera_aim_height_offset": 0x0982,
	"intro_motion_speed": 0x09A2,
	"replay_motion_speed": 0x09C6,
	"final_motion_speed": 0x09F0,
	"final_motion_replay_speed": 0x0AA4,
}


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
			pc_to_string_index[pc] = int(instruction.get("decoded", {}).get("index"))
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


def read_push_float(pc_to_instruction, pc):
	instruction = expect_instruction(pc_to_instruction, pc, "push_s_f")
	return float(instruction.get("decoded", {}).get("value"))


def write_push_float(pc_to_instruction, pc, value):
	instruction = expect_instruction(pc_to_instruction, pc, "push_s_f")
	float_value = float(value)
	instruction["payload_hex"] = struct.pack(">f", float_value).hex().upper()
	instruction["decoded"] = {"type": "float", "value": float_value}


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
			"linked_event_scripts": read_string_list(string_entries, pc_to_string_index, COMMON_NEWITEM_STRING_PCS),
			"money_item_script": read_push_str(string_entries, pc_to_string_index, MONEY_ITEM_SCRIPT_PC)[0],
		},
		"rival_scene": {
			"strings": {key: read_push_str(string_entries, pc_to_string_index, pcs[0])[0] for key, pcs in RIVAL_STRING_GROUPS.items()},
			"integers": {key: read_push_int(pc_to_instruction, pc) for key, pc in RIVAL_INT_PCS.items()},
			"floats": {key: read_push_float(pc_to_instruction, pc) for key, pc in RIVAL_FLOAT_PCS.items()},
		},
	}


def apply_semantic(document, semantic):
	if semantic.get("format") != FORMAT_VERSION:
		raise ValueError(f"Unsupported JSON format: {semantic.get('format')!r}")
	pc_to_instruction, pc_to_string_index = build_pc_maps(document)
	string_entries = document["string_section"]["entries"]
	write_push_str_group(string_entries, pc_to_string_index, COMMON_NEWITEM_STRING_PCS, semantic["embedded_scripts"]["linked_event_scripts"])
	write_push_str_group(string_entries, pc_to_string_index, [MONEY_ITEM_SCRIPT_PC], [semantic["embedded_scripts"]["money_item_script"]])
	for key, pcs in RIVAL_STRING_GROUPS.items():
		write_push_str_group(string_entries, pc_to_string_index, pcs, [semantic["rival_scene"]["strings"][key]] * len(pcs))
	for key, pc in RIVAL_INT_PCS.items():
		write_push_int(pc_to_instruction, pc, semantic["rival_scene"]["integers"][key])
	for key, pc in RIVAL_FLOAT_PCS.items():
		write_push_float(pc_to_instruction, pc, semantic["rival_scene"]["floats"][key])
	return document

def command_summary(args):
	document = decode_pkc(args.input)
	semantic = export_semantic(document)
	print(f"Linked event scripts: {len(semantic['embedded_scripts']['linked_event_scripts'])}")
	print(f"Rival event data: {semantic['rival_scene']['strings']['event_data_name']}")
	print(f"Layout archive: {semantic['rival_scene']['strings']['layout_archive_path']}")
	print(f"Intro effect species: {semantic['rival_scene']['integers']['intro_effect_species']}")


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
	rebuilt = encode_pkc_document(rebuilt_document)
	with open(args.input, "rb") as infile:
		original = infile.read()
	if rebuilt != original:
		print("Roundtrip FAILED", file=sys.stderr)
		for index, (left, right) in enumerate(zip(original, rebuilt)):
			if left != right:
				print(f"First difference at 0x{index:X}: original=0x{left:02X}, rebuilt=0x{right:02X}", file=sys.stderr)
				break
		if len(original) != len(rebuilt):
			print(f"Length differs: original={len(original)} rebuilt={len(rebuilt)}", file=sys.stderr)
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
