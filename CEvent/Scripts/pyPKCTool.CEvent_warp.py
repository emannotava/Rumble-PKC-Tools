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


TOOL_DESCRIPTION = "Edit CEvent_warp.pkc via semantic JSON."
FORMAT_VERSION = "cevent-warp-semantic-json-1"
COMMON_NEWITEM_STRING_PCS = [
	0x0394, 0x039A, 0x03A0, 0x03A6, 0x03AC, 0x03B2, 0x03B8, 0x03BE,
	0x03C4, 0x03CA, 0x03D0, 0x03D6, 0x03DC, 0x03E2, 0x03E8, 0x03EE,
	0x040A,
]
MONEY_ITEM_SCRIPT_PC = 0x075F
CHOICE_MESSAGE_MID_PCS = [0x09D2, 0x09D9, 0x09DD, 0x09E1, 0x09E5]
STAGE_PROGRESS_PCS = {
	"map_status_flag_a": 0x0972,
	"map_status_flag_b": 0x0976,
	"unlock_flag_id": 0x0992,
	"round_clear_stat_id": 0x099D,
	"round_clear_stat_add": 0x099E,
	"endstage_success_flag": 0x09A2,
	"endstage_success_reason": 0x09A3,
	"endstage_fallback_flag": 0x09A8,
	"endstage_fallback_reason": 0x09A9,
}
WARP_MOTION_GROUPS = {
	"rank_s": [0x0A98, 0x0A9C, 0x0AA0],
	"rank_a": [0x0AA5, 0x0AA9, 0x0AAD],
	"rank_b": [0x0AB2, 0x0AB6, 0x0ABA],
	"rank_c": [0x0ABF, 0x0AC3, 0x0AC7],
	"default_large": [0x0AD3, 0x0AD7, 0x0ADB],
	"default_small": [0x0AE0, 0x0AE4, 0x0AE8],
}
WARP_EFFECT_INT_PCS = {
	"start_loop_se_id": 0x0AEC,
	"primary_motion_id": 0x0AF0,
	"teleport_se_id": 0x0B2E,
	"teleport_effect_id": 0x0B32,
	"teleport_effect_dir_deg": 0x0B33,
	"finish_motion_id": 0x0B37,
}
WARP_RUNTIME_FLOAT_PCS = {
	"distance_ceiling": 0x0A1A,
	"dir_blend_factor": 0x0B1C,
}
WARP_RUNTIME_INT_PCS = {
	"scan_player_index": 0x09CC,
	"choice_default_index": 0x09F6,
	"warp_animation_motion_id": 0x0A78,
}

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
		"battle_royal_choice": {
			"message_mids": read_int_list(pc_to_instruction, CHOICE_MESSAGE_MID_PCS),
			"scan_player_index": read_push_int(pc_to_instruction, WARP_RUNTIME_INT_PCS["scan_player_index"]),
			"choice_default_index": read_push_int(pc_to_instruction, WARP_RUNTIME_INT_PCS["choice_default_index"]),
		},
		"stage_progression": {key: read_push_int(pc_to_instruction, pc) for key, pc in STAGE_PROGRESS_PCS.items()},
		"warp_motions": {key: {
			"begin": read_push_str(string_entries, pc_to_string_index, pcs[0])[0],
			"loop": read_push_str(string_entries, pc_to_string_index, pcs[1])[0],
			"end": read_push_str(string_entries, pc_to_string_index, pcs[2])[0],
		} for key, pcs in WARP_MOTION_GROUPS.items()},
		"warp_effects": {
			"integers": {key: read_push_int(pc_to_instruction, pc) for key, pc in WARP_EFFECT_INT_PCS.items()},
			"floats": {key: read_push_float(pc_to_instruction, pc) for key, pc in WARP_RUNTIME_FLOAT_PCS.items()},
			"runtime_motion_ids": {
				"warp_animation_motion_id": read_push_int(pc_to_instruction, WARP_RUNTIME_INT_PCS["warp_animation_motion_id"]),
			},
		},
	}


def apply_semantic(document, semantic):
	if semantic.get("format") != FORMAT_VERSION:
		raise ValueError(f"Unsupported JSON format: {semantic.get('format')!r}")
	pc_to_instruction, pc_to_string_index = build_pc_maps(document)
	string_entries = document["string_section"]["entries"]
	write_push_str_group(string_entries, pc_to_string_index, COMMON_NEWITEM_STRING_PCS, semantic["embedded_scripts"]["linked_event_scripts"])
	write_push_str_group(string_entries, pc_to_string_index, [MONEY_ITEM_SCRIPT_PC], [semantic["embedded_scripts"]["money_item_script"]])
	write_int_list(pc_to_instruction, CHOICE_MESSAGE_MID_PCS, semantic["battle_royal_choice"]["message_mids"], "battle_royal_choice.message_mids")
	write_push_int(pc_to_instruction, WARP_RUNTIME_INT_PCS["scan_player_index"], semantic["battle_royal_choice"]["scan_player_index"])
	write_push_int(pc_to_instruction, WARP_RUNTIME_INT_PCS["choice_default_index"], semantic["battle_royal_choice"]["choice_default_index"])
	for key, pc in STAGE_PROGRESS_PCS.items():
		write_push_int(pc_to_instruction, pc, semantic["stage_progression"][key])
	for key, pcs in WARP_MOTION_GROUPS.items():
		entry = semantic["warp_motions"][key]
		write_push_str_group(string_entries, pc_to_string_index, pcs, [entry["begin"], entry["loop"], entry["end"]])
	for key, pc in WARP_EFFECT_INT_PCS.items():
		write_push_int(pc_to_instruction, pc, semantic["warp_effects"]["integers"][key])
	for key, pc in WARP_RUNTIME_FLOAT_PCS.items():
		write_push_float(pc_to_instruction, pc, semantic["warp_effects"]["floats"][key])
	write_push_int(pc_to_instruction, WARP_RUNTIME_INT_PCS["warp_animation_motion_id"], semantic["warp_effects"]["runtime_motion_ids"]["warp_animation_motion_id"])
	return document


def command_summary(args):
	document = decode_pkc(args.input)
	semantic = export_semantic(document)
	print(f"Choice messages: {len(semantic['battle_royal_choice']['message_mids'])}")
	print(f"Unlock flag: {semantic['stage_progression']['unlock_flag_id']}")
	print(f"Default large warp motion: {semantic['warp_motions']['default_large']['begin']}")

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
