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


TOOL_DESCRIPTION = "Edit CEvent_prologue2.pkc via semantic JSON."
FORMAT_VERSION = "cevent-prologue2-semantic-json-1"

COMMON_NEWITEM_STRING_PCS = [
	0x0394, 0x039A, 0x03A0, 0x03A6, 0x03AC, 0x03B2, 0x03B8, 0x03BE,
	0x03C4, 0x03CA, 0x03D0, 0x03D6, 0x03DC, 0x03E2, 0x03E8, 0x03EE,
	0x040A,
]
MONEY_ITEM_SCRIPT_PC = 0x075F
DIRECTION_SUFFIX_PCS = {
	"center": 0x0901,
	"north": 0x0904,
	"east": 0x0907,
	"south": 0x090A,
	"west": 0x090D,
	"northeast": 0x0910,
	"southeast": 0x0913,
	"northwest": 0x0916,
	"southwest": 0x0919,
}
LAYOUT_FLOAT_PCS = {
	"text_scale_x": 0x093C,
	"text_scale_y": 0x093F,
	"play_speed": 0x0943,
	"request_next_speed": 0x094A,
}

INTRO_LAYOUT_STRINGS = {
	"archive_path": 0x096D,
	"layout_name": 0x096E,
	"play_motion": 0x0972,
}
GATE_SETUP_STRINGS = {
	"event_name": 0x0979,
	"gate_model_brres": 0x097D,
	"gate_play_motion": 0x0989,
	"camera_replay_motion": 0x098F,
	"helper_item_script_a": 0x099C,
	"koratta_model_brres": 0x09A2,
	"koratta_play_motion": 0x09AA,
	"helper_item_script_b": 0x09B0,
	"warppoint_model_a_brres": 0x09B6,
	"helper_item_script_c": 0x09C4,
	"warppoint_model_b_brres": 0x09CA,
	"end_event_name": 0x0A02,
	"end_motion": 0x0A06,
}
GATE_SETUP_INTS = {
	"show_minimap_flag_on_enter": 0x0976,
	"gate_enable_bounding": 0x0981,
	"gate_show_flag": 0x0985,
	"koratta_show_flag": 0x09A6,
	"koratta_se_id": 0x09AD,
	"warppoint_a_show_flag": 0x09BA,
	"warppoint_b_show_flag": 0x09CE,
	"fade_flag": 0x09D9,
	"fade_frames": 0x09DA,
	"effect_a_id": 0x09E1,
	"effect_a_dir_deg": 0x09E2,
	"effect_a_se_id": 0x09E5,
	"effect_b_id": 0x09EC,
	"effect_b_dir_deg": 0x09ED,
	"effect_b_se_id": 0x09F0,
	"extra_se_id": 0x09F3,
	"show_post_effect_id": 0x09F9,
}
GATE_SETUP_INT_POSITIONS = {
	"warppoint_a_position_x": 0x09BE,
	"warppoint_a_position_y": 0x09BF,
	"warppoint_b_position_x": 0x09D2,
	"warppoint_b_position_y": 0x09D4,
}
GATE_SETUP_FLOATS = {
	"koratta_spawn_y_offset": 0x0997,
	"warppoint_a_position_z": 0x09C0,
	"warppoint_b_position_z": 0x09D5,
}
MESSAGE_MID_PCS = [0x0A25, 0x0A30, 0x0A3B, 0x0A46]
BGM_STAGE_DESC_PARAM_INDEX_PC = 0x0A1D
SHOW_MINIMAP_FLAG_EXIT_PC = 0x0A51

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
		"direction_suffixes": {key: read_push_str(string_entries, pc_to_string_index, pc)[0] for key, pc in DIRECTION_SUFFIX_PCS.items()},
		"layout_animation": {key: read_push_float(pc_to_instruction, pc) for key, pc in LAYOUT_FLOAT_PCS.items()},
		"intro_layout": {key: read_push_str(string_entries, pc_to_string_index, pc)[0] for key, pc in INTRO_LAYOUT_STRINGS.items()},
		"gate_setup": {
			"strings": {key: read_push_str(string_entries, pc_to_string_index, pc)[0] for key, pc in GATE_SETUP_STRINGS.items()},
			"integers": {key: read_push_int(pc_to_instruction, pc) for key, pc in GATE_SETUP_INTS.items()},
			"floats": ({key: read_push_float(pc_to_instruction, pc) for key, pc in GATE_SETUP_FLOATS.items()} | {key: read_push_int(pc_to_instruction, pc) for key, pc in GATE_SETUP_INT_POSITIONS.items()}),
		},
		"message_sequence_mids": read_int_list(pc_to_instruction, MESSAGE_MID_PCS),
		"bgm_stage_desc_param_index": read_push_int(pc_to_instruction, BGM_STAGE_DESC_PARAM_INDEX_PC),
		"show_minimap_flag_on_exit": read_push_int(pc_to_instruction, SHOW_MINIMAP_FLAG_EXIT_PC),
	}


def apply_semantic(document, semantic):
	if semantic.get("format") != FORMAT_VERSION:
		raise ValueError(f"Unsupported JSON format: {semantic.get('format')!r}")
	pc_to_instruction, pc_to_string_index = build_pc_maps(document)
	string_entries = document["string_section"]["entries"]
	write_push_str_group(string_entries, pc_to_string_index, COMMON_NEWITEM_STRING_PCS, semantic["embedded_scripts"]["linked_event_scripts"])
	write_push_str_group(string_entries, pc_to_string_index, [MONEY_ITEM_SCRIPT_PC], [semantic["embedded_scripts"]["money_item_script"]])
	for key, pc in DIRECTION_SUFFIX_PCS.items():
		write_push_str_group(string_entries, pc_to_string_index, [pc], [semantic["direction_suffixes"][key]])
	for key, pc in LAYOUT_FLOAT_PCS.items():
		write_push_float(pc_to_instruction, pc, semantic["layout_animation"][key])
	for key, pc in INTRO_LAYOUT_STRINGS.items():
		write_push_str_group(string_entries, pc_to_string_index, [pc], [semantic["intro_layout"][key]])
	for key, pc in GATE_SETUP_STRINGS.items():
		write_push_str_group(string_entries, pc_to_string_index, [pc], [semantic["gate_setup"]["strings"][key]])
	for key, pc in GATE_SETUP_INTS.items():
		write_push_int(pc_to_instruction, pc, semantic["gate_setup"]["integers"][key])
	for key, pc in GATE_SETUP_FLOATS.items():
		write_push_float(pc_to_instruction, pc, semantic["gate_setup"]["floats"][key])
	for key, pc in GATE_SETUP_INT_POSITIONS.items():
		write_push_int(pc_to_instruction, pc, semantic["gate_setup"]["floats"][key])
	write_int_list(pc_to_instruction, MESSAGE_MID_PCS, semantic["message_sequence_mids"], "message_sequence_mids")
	write_push_int(pc_to_instruction, BGM_STAGE_DESC_PARAM_INDEX_PC, semantic["bgm_stage_desc_param_index"])
	write_push_int(pc_to_instruction, SHOW_MINIMAP_FLAG_EXIT_PC, semantic["show_minimap_flag_on_exit"])
	return document


def command_summary(args):
	document = decode_pkc(args.input)
	semantic = export_semantic(document)
	print(f"Intro layout: {semantic['intro_layout']['layout_name']}")
	print(f"Gate event: {semantic['gate_setup']['strings']['event_name']}")
	print(f"Message count: {len(semantic['message_sequence_mids'])}")

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


def read_float_list(pc_to_instruction, pcs):
	return [read_push_float(pc_to_instruction, pc) for pc in pcs]


def write_int_list(pc_to_instruction, pcs, values, label):
	if len(values) != len(pcs):
		raise ValueError(f"{label} must contain {len(pcs)} values, got {len(values)}")
	for pc, value in zip(pcs, values):
		write_push_int(pc_to_instruction, pc, value)


def write_float_list(pc_to_instruction, pcs, values, label):
	if len(values) != len(pcs):
		raise ValueError(f"{label} must contain {len(pcs)} values, got {len(values)}")
	for pc, value in zip(pcs, values):
		write_push_float(pc_to_instruction, pc, value)


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


def build_parser():
	parser = argparse.ArgumentParser(description=TOOL_DESCRIPTION)
	subparsers = parser.add_subparsers(dest="command", required=True)
	summary_parser = subparsers.add_parser("summary", help="Show a short semantic summary.")
	summary_parser.add_argument("input", help="Input PKC file")
	summary_parser.set_defaults(func=command_summary)
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
	parser = build_parser()
	args = parser.parse_args()
	args.func(args)


if __name__ == "__main__":
	main()
