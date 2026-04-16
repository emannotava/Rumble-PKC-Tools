#!/usr/bin/env python3
import argparse
import copy
import json
import os
import sys
import struct

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
	sys.path.insert(0, SCRIPT_DIR)

try:
	from pyPKCToolBase import decode_pkc, encode_pkc_document
except ImportError as exc:
	print(f"Failed to import pyPKCToolBase.py: {exc}", file=sys.stderr)
	sys.exit(1)

FORMAT_VERSION = "citem-nakama-semantic-json-1"

EVENT_SCRIPT_PCS = [
	0x0394, 0x039A, 0x03A0, 0x03A6, 0x03AC, 0x03B2, 0x03B8, 0x03BE,
	0x03C4, 0x03CA, 0x03D0, 0x03D6, 0x03DC, 0x03E2, 0x03E8, 0x03EE,
	0x040A,
]
MONEY_ITEM_SCRIPT_PCS = [0x075F]
DUMMY_BALL_SCRIPT_PCS = [0x1B5D, 0x1C1A, 0x1CCE]

COMMON_MESSAGE_PC_MAP = {
	"dynamic_category_base_high": 0x056A,
	"dynamic_category_base_low": 0x0575,
	"difficulty_rank_labels": [0x0858, 0x085D, 0x0862, 0x086E, 0x0873, 0x0878, 0x087D, 0x0882],
	"team_box_full": 0x16F5,
	"low_battery": 0x1752,
}

ENERGY_TIERS = [
	{"threshold_pc": 0x1651, "model_pc": 0x1655, "loop_pc": 0x1659},
	{"threshold_pc": 0x1660, "model_pc": 0x1664, "loop_pc": 0x1668},
	{"threshold_pc": 0x166D, "model_pc": 0x1671, "loop_pc": 0x1675},
	{"threshold_pc": None, "model_pc": 0x167A, "loop_pc": 0x167E},
]

TERMINAL_VISUAL_PCS = {
	"show_on_init": 0x1760,
	"dir_deg": 0x1764,
	"model_brres": 0x1769,
	"password_found_token": 0x178C,
	"fall_motion": 0x1A2D,
}

GAMEPLAY_CONSTANTS = {
	"team_box_capacity": [0x16E4, 0x16ED],
	"low_battery_controller_index": 0x174B,
	"dummy_ball_spawn_height": [0x1B65, 0x1C22, 0x1CD6],
	"dummy_ball_spawn_dir_deg": [0x1B30, 0x1BED, 0x1CA0],
	"nandsave_wait_frames": [0x1B58, 0x1C15],
}

EXCLUDED_MESSAGE_PCS = set(COMMON_MESSAGE_PC_MAP["difficulty_rank_labels"] + [
	COMMON_MESSAGE_PC_MAP["dynamic_category_base_high"],
	COMMON_MESSAGE_PC_MAP["dynamic_category_base_low"],
	COMMON_MESSAGE_PC_MAP["team_box_full"],
	COMMON_MESSAGE_PC_MAP["low_battery"],
])


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
	instructions = document["code_section"]["instructions"]
	for index, instruction in enumerate(instructions):
		pc = int(instruction["pc_units"])
		pc_to_instruction[pc] = instruction
		instruction["_scan_index"] = index
		if instruction["opname"] == "push_str":
			decoded = instruction.get("decoded", {})
			pc_to_string_index[pc] = int(decoded["index"])
	return instructions, pc_to_instruction, pc_to_string_index


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
	instruction["decoded"] = {
		"type": "int24",
		"value_signed": int(value),
		"value_unsigned": instruction["imm"],
	}


def read_push_float(pc_to_instruction, pc):
	instruction = expect_instruction(pc_to_instruction, pc, "push_s_f")
	return float(instruction.get("decoded", {}).get("value"))


def write_push_float(pc_to_instruction, pc, value):
	instruction = expect_instruction(pc_to_instruction, pc, "push_s_f")
	float_value = float(value)
	instruction["payload_hex"] = struct.pack(">f", float_value).hex().upper()
	instruction["decoded"] = {
		"type": "float",
		"value": float_value,
		"source_text": repr(float_value),
		"bit_pattern_hex": instruction["payload_hex"],
	}


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
			raise ValueError(
				f"Conflicting values for shared string index {index}: {index_to_value[index]!r} vs {value!r}"
			)
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


def scan_catgets_message_ids(instructions):
	rows = []
	for index, instruction in enumerate(instructions):
		decoded = instruction.get("decoded", {})
		if decoded.get("type") != "external_call" or decoded.get("function_name") != "CatGets":
			continue
		if index < 2:
			continue
		push_dat = instructions[index - 1]
		push_int = instructions[index - 2]
		if push_dat["opname"] != "push_dat" or push_int["opname"] != "push_int":
			continue
		pc = int(push_int["pc_units"])
		if pc in EXCLUDED_MESSAGE_PCS:
			continue
		rows.append({
			"pc": f"0x{pc:04X}",
			"message_id": int(push_int["decoded"]["value_signed"]),
		})
	return rows


def scan_waza_desc_field_indexes(instructions):
	rows = []
	for index, instruction in enumerate(instructions):
		decoded = instruction.get("decoded", {})
		if decoded.get("type") != "external_call" or decoded.get("function_name") != "GetWazaDesc":
			continue
		if index < 1 or instructions[index - 1]["opname"] != "push_int":
			continue
		field_instruction = instructions[index - 1]
		rows.append({
			"pc": f"0x{int(field_instruction['pc_units']):04X}",
			"field_index": int(field_instruction["decoded"]["value_signed"]),
		})
	return rows


def scan_ppr_get_field_indexes(instructions):
	rows = []
	for index, instruction in enumerate(instructions):
		decoded = instruction.get("decoded", {})
		if decoded.get("type") != "external_call" or decoded.get("function_name") != "method_pprGet":
			continue
		if index < 1 or instructions[index - 1]["opname"] != "push_int":
			continue
		field_instruction = instructions[index - 1]
		rows.append({
			"pc": f"0x{int(field_instruction['pc_units']):04X}",
			"field_index": int(field_instruction["decoded"]["value_signed"]),
		})
	return rows


def scan_waza_level_sections(instructions):
	rows = []
	for index, instruction in enumerate(instructions):
		decoded = instruction.get("decoded", {})
		if decoded.get("type") != "external_call" or decoded.get("function_name") != "method_pprGetWazaLevel":
			continue
		if index < 2:
			continue
		segment_instruction = instructions[index - 2]
		section_instruction = instructions[index - 1]
		if segment_instruction["opname"] != "push_int" or section_instruction["opname"] != "push_int":
			continue
		rows.append({
			"pc_segment": f"0x{int(segment_instruction['pc_units']):04X}",
			"segment": int(segment_instruction["decoded"]["value_signed"]),
			"pc_section": f"0x{int(section_instruction['pc_units']):04X}",
			"section_id": int(section_instruction["decoded"]["value_signed"]),
		})
	return rows


def export_semantic(document):
	instructions, pc_to_instruction, pc_to_string_index = build_pc_maps(document)
	string_entries = document["string_section"]["entries"]
	return {
		"format": FORMAT_VERSION,
		"source_basename": document.get("source_basename"),
		"embedded_scripts": {
			"event_item_sequence": read_string_list(string_entries, pc_to_string_index, EVENT_SCRIPT_PCS),
			"money_item_script": read_push_str(string_entries, pc_to_string_index, MONEY_ITEM_SCRIPT_PCS[0])[0],
			"dummy_ball_script": read_push_str(string_entries, pc_to_string_index, DUMMY_BALL_SCRIPT_PCS[0])[0],
		},
		"common_messages": {
			"dynamic_category_base_high": read_push_int(pc_to_instruction, COMMON_MESSAGE_PC_MAP["dynamic_category_base_high"]),
			"dynamic_category_base_low": read_push_int(pc_to_instruction, COMMON_MESSAGE_PC_MAP["dynamic_category_base_low"]),
			"difficulty_rank_labels": read_int_list(pc_to_instruction, COMMON_MESSAGE_PC_MAP["difficulty_rank_labels"]),
			"team_box_full": read_push_int(pc_to_instruction, COMMON_MESSAGE_PC_MAP["team_box_full"]),
			"low_battery": read_push_int(pc_to_instruction, COMMON_MESSAGE_PC_MAP["low_battery"]),
		},
		"energy_visuals": {
			"tiers": [
				{
					"threshold_lt": None if config["threshold_pc"] is None else read_push_int(pc_to_instruction, config["threshold_pc"]),
					"model_brres": read_push_str(string_entries, pc_to_string_index, config["model_pc"])[0],
					"loop_motion": read_push_str(string_entries, pc_to_string_index, config["loop_pc"])[0],
				}
				for config in ENERGY_TIERS
			],
		},
		"terminal_visuals": {
			"show_on_init": read_push_int(pc_to_instruction, TERMINAL_VISUAL_PCS["show_on_init"]),
			"dir_deg": read_push_float(pc_to_instruction, TERMINAL_VISUAL_PCS["dir_deg"]),
			"model_brres": read_push_str(string_entries, pc_to_string_index, TERMINAL_VISUAL_PCS["model_brres"])[0],
			"password_found_token": read_push_str(string_entries, pc_to_string_index, TERMINAL_VISUAL_PCS["password_found_token"])[0],
			"fall_motion": read_push_str(string_entries, pc_to_string_index, TERMINAL_VISUAL_PCS["fall_motion"])[0],
		},
		"additional_message_ids": scan_catgets_message_ids(instructions),
		"field_indexes": {
			"waza_desc": scan_waza_desc_field_indexes(instructions),
			"ppr_get": scan_ppr_get_field_indexes(instructions),
			"waza_level_sections": scan_waza_level_sections(instructions),
		},
		"gameplay_constants": {
			"team_box_capacity": read_push_int(pc_to_instruction, GAMEPLAY_CONSTANTS["team_box_capacity"][0]),
			"low_battery_controller_index": read_push_int(pc_to_instruction, GAMEPLAY_CONSTANTS["low_battery_controller_index"]),
			"dummy_ball_spawn_height": [read_push_float(pc_to_instruction, pc) for pc in GAMEPLAY_CONSTANTS["dummy_ball_spawn_height"]],
			"dummy_ball_spawn_dir_deg": read_int_list(pc_to_instruction, GAMEPLAY_CONSTANTS["dummy_ball_spawn_dir_deg"]),
			"nandsave_wait_frames": read_int_list(pc_to_instruction, GAMEPLAY_CONSTANTS["nandsave_wait_frames"]),
		},
	}


def apply_semantic(document, semantic):
	if semantic.get("format") != FORMAT_VERSION:
		raise ValueError(f"Unsupported JSON format: {semantic.get('format')!r}")
	instructions, pc_to_instruction, pc_to_string_index = build_pc_maps(document)
	string_entries = document["string_section"]["entries"]

	write_push_str_group(string_entries, pc_to_string_index, EVENT_SCRIPT_PCS, semantic["embedded_scripts"]["event_item_sequence"])
	write_push_str_group(string_entries, pc_to_string_index, MONEY_ITEM_SCRIPT_PCS, [semantic["embedded_scripts"]["money_item_script"]])
	write_push_str_group(string_entries, pc_to_string_index, DUMMY_BALL_SCRIPT_PCS, [semantic["embedded_scripts"]["dummy_ball_script"]] * len(DUMMY_BALL_SCRIPT_PCS))

	write_push_int(pc_to_instruction, COMMON_MESSAGE_PC_MAP["dynamic_category_base_high"], semantic["common_messages"]["dynamic_category_base_high"])
	write_push_int(pc_to_instruction, COMMON_MESSAGE_PC_MAP["dynamic_category_base_low"], semantic["common_messages"]["dynamic_category_base_low"])
	write_int_list(pc_to_instruction, COMMON_MESSAGE_PC_MAP["difficulty_rank_labels"], semantic["common_messages"]["difficulty_rank_labels"], "common_messages.difficulty_rank_labels")
	write_push_int(pc_to_instruction, COMMON_MESSAGE_PC_MAP["team_box_full"], semantic["common_messages"]["team_box_full"])
	write_push_int(pc_to_instruction, COMMON_MESSAGE_PC_MAP["low_battery"], semantic["common_messages"]["low_battery"])

	tiers = semantic["energy_visuals"]["tiers"]
	if len(tiers) != len(ENERGY_TIERS):
		raise ValueError(f"energy_visuals.tiers must contain {len(ENERGY_TIERS)} rows")
	for config, entry in zip(ENERGY_TIERS, tiers):
		if config["threshold_pc"] is not None:
			write_push_int(pc_to_instruction, config["threshold_pc"], entry["threshold_lt"])
		write_push_str_group(string_entries, pc_to_string_index, [config["model_pc"]], [entry["model_brres"]])
		write_push_str_group(string_entries, pc_to_string_index, [config["loop_pc"]], [entry["loop_motion"]])

	write_push_int(pc_to_instruction, TERMINAL_VISUAL_PCS["show_on_init"], semantic["terminal_visuals"]["show_on_init"])
	write_push_float(pc_to_instruction, TERMINAL_VISUAL_PCS["dir_deg"], semantic["terminal_visuals"]["dir_deg"])
	for key in ("model_brres", "password_found_token", "fall_motion"):
		write_push_str_group(string_entries, pc_to_string_index, [TERMINAL_VISUAL_PCS[key]], [semantic["terminal_visuals"][key]])

	for row in semantic.get("additional_message_ids", []):
		pc = int(str(row["pc"]), 16)
		write_push_int(pc_to_instruction, pc, row["message_id"])

	for row in semantic["field_indexes"]["waza_desc"]:
		write_push_int(pc_to_instruction, int(str(row["pc"]), 16), row["field_index"])
	for row in semantic["field_indexes"]["ppr_get"]:
		write_push_int(pc_to_instruction, int(str(row["pc"]), 16), row["field_index"])
	for row in semantic["field_indexes"]["waza_level_sections"]:
		write_push_int(pc_to_instruction, int(str(row["pc_segment"]), 16), row["segment"])
		write_push_int(pc_to_instruction, int(str(row["pc_section"]), 16), row["section_id"])

	for pc in GAMEPLAY_CONSTANTS["team_box_capacity"]:
		write_push_int(pc_to_instruction, pc, semantic["gameplay_constants"]["team_box_capacity"])
	write_push_int(pc_to_instruction, GAMEPLAY_CONSTANTS["low_battery_controller_index"], semantic["gameplay_constants"]["low_battery_controller_index"])
	for pc, value in zip(GAMEPLAY_CONSTANTS["dummy_ball_spawn_height"], semantic["gameplay_constants"]["dummy_ball_spawn_height"]):
		write_push_float(pc_to_instruction, pc, value)
	write_int_list(pc_to_instruction, GAMEPLAY_CONSTANTS["dummy_ball_spawn_dir_deg"], semantic["gameplay_constants"]["dummy_ball_spawn_dir_deg"], "gameplay_constants.dummy_ball_spawn_dir_deg")
	write_int_list(pc_to_instruction, GAMEPLAY_CONSTANTS["nandsave_wait_frames"], semantic["gameplay_constants"]["nandsave_wait_frames"], "gameplay_constants.nandsave_wait_frames")
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
	parser = argparse.ArgumentParser(description="Edit CItem_nakama.pkc via semantic JSON.")
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
