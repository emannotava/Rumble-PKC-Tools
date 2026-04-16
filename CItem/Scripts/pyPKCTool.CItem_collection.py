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


FORMAT_VERSION = "citem-semantic-json-2"

NEWITEM_STRING_PCS = [
	0x0394,
	0x039A,
	0x03A0,
	0x03A6,
	0x03AC,
	0x03B2,
	0x03B8,
	0x03BE,
	0x03C4,
	0x03CA,
	0x03D0,
	0x03D6,
	0x03DC,
	0x03E2,
	0x03E8,
	0x03EE,
	0x040A,
	0x075F,
]

MODEL_BRRES_PC = 0x0985
FALL_MOTION_PC = 0x09DD

MESSAGE_PC_MAP = {
	"dynamic_category_base_high": 0x056A,
	"dynamic_category_base_low": 0x0575,
	"difficulty_rank_labels": [
		0x0858,
		0x085D,
		0x0862,
		0x086E,
		0x0873,
		0x0878,
		0x087D,
		0x0882,
	],
	"team_box_full": 0x0911,
	"low_battery": 0x096E,
	"init_balloon": 0x09C6,
	"multiplayer_prompt": 0x09EF,
}

FLAG_PC_MAP = {
	"availability_flag": 0x02D6,
	"open_gate_flag": 0x0483,
	"fall_animation_flag": [0x09CD, 0x09D5],
}

DIALOG_PC_MAP = {
	"team_box_capacity": [0x0909, 0x0915],
	"low_battery_controller_index": 0x0967,
	"register_message_balloon_slot": 0x09C5,
}

WAZA_DESC_FIELD_PCS = [
	0x08A6,
	0x08AA,
	0x08AE,
	0x08B2,
	0x08B6,
]

RANDOM_WEIGHT_TRIPLE_PCS = [
	[0x0786, 0x0788, 0x078A],
	[0x078D, 0x078F, 0x0791],
	[0x0794, 0x0796, 0x0798],
	[0x079B, 0x079D, 0x079F],
	[0x07A7, 0x07A9, 0x07AB],
	[0x07AE, 0x07B0, 0x07B2],
	[0x07B5, 0x07B7, 0x07B9],
	[0x07BC, 0x07BE, 0x07C0],
	[0x07C6, 0x07C8, 0x07CA],
]

RANDOM_WEIGHT_OUTPUT_PCS = {
	"first": 0x07DF,
	"second": 0x07E8,
	"third": 0x07EB,
	"fallback": 0x07ED,
}

STAGE_VALUE_TABLE_A_PCS = [
	0x07FF,
	0x0801,
	0x0803,
	0x0805,
	0x080B,
	0x080D,
	0x080F,
	0x0811,
	0x0816,
]

STAGE_VALUE_TABLE_B_PCS = [
	0x082E,
	0x0830,
	0x0832,
	0x0834,
	0x083A,
	0x083C,
	0x083E,
	0x0840,
	0x0845,
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
		raise ValueError(
			f"Expected {opname} at PC 0x{pc:04X}, found {instruction['opname']}"
		)
	return instruction


def read_push_int(pc_to_instruction, pc):
	instruction = expect_instruction(pc_to_instruction, pc, "push_int")
	decoded = instruction.get("decoded", {})
	return int(decoded.get("value_signed"))


def write_push_int(pc_to_instruction, pc, value):
	instruction = expect_instruction(pc_to_instruction, pc, "push_int")
	instruction["imm"] = signed24_to_u24(value)
	instruction["imm_hex"] = f"0x{instruction['imm']:06X}"
	instruction["decoded"] = {
		"type": "int24",
		"value_signed": int(value),
		"value_unsigned": instruction["imm"],
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
				f"Conflicting values for shared string index {index}: "
				f"{index_to_value[index]!r} vs {value!r}"
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
		raise ValueError(
			f"{label} must contain {len(pcs)} values, got {len(values)}"
		)
	for pc, value in zip(pcs, values):
		write_push_int(pc_to_instruction, pc, value)


def read_variant(document):
	pc_to_instruction, pc_to_string_index = build_pc_maps(document)
	string_entries = document["string_section"]["entries"]
	model_name, _ = read_push_str(string_entries, pc_to_string_index, MODEL_BRRES_PC)
	if "multiplay" in model_name:
		return "multi"
	return "collection"


def export_semantic(document):
	pc_to_instruction, pc_to_string_index = build_pc_maps(document)
	string_entries = document["string_section"]["entries"]
	variant = read_variant(document)

	semantic = {
		"format": FORMAT_VERSION,
		"source_basename": document.get("source_basename"),
		"variant": variant,
		"embedded_scripts": {
			"newitem_sequence": read_string_list(
				string_entries,
				pc_to_string_index,
				NEWITEM_STRING_PCS,
			),
			"model_brres": read_push_str(
				string_entries,
				pc_to_string_index,
				MODEL_BRRES_PC,
			)[0],
			"fall_motion": read_push_str(
				string_entries,
				pc_to_string_index,
				FALL_MOTION_PC,
			)[0],
		},
		"messages": {
			"dynamic_category_base_high": read_push_int(
				pc_to_instruction,
				MESSAGE_PC_MAP["dynamic_category_base_high"],
			),
			"dynamic_category_base_low": read_push_int(
				pc_to_instruction,
				MESSAGE_PC_MAP["dynamic_category_base_low"],
			),
			"difficulty_rank_labels": read_int_list(
				pc_to_instruction,
				MESSAGE_PC_MAP["difficulty_rank_labels"],
			),
			"team_box_full": read_push_int(
				pc_to_instruction,
				MESSAGE_PC_MAP["team_box_full"],
			),
			"low_battery": read_push_int(
				pc_to_instruction,
				MESSAGE_PC_MAP["low_battery"],
			),
			"init_balloon": read_push_int(
				pc_to_instruction,
				MESSAGE_PC_MAP["init_balloon"],
			),
			"multiplayer_prompt": read_push_int(
				pc_to_instruction,
				MESSAGE_PC_MAP["multiplayer_prompt"],
			) if variant == "multi" else None,
		},
		"flags": {
			"availability_flag": read_push_int(
				pc_to_instruction,
				FLAG_PC_MAP["availability_flag"],
			),
			"open_gate_flag": read_push_int(
				pc_to_instruction,
				FLAG_PC_MAP["open_gate_flag"],
			),
			"fall_animation_flag": read_push_int(
				pc_to_instruction,
				FLAG_PC_MAP["fall_animation_flag"][0],
			),
		},
		"dialogs": {
			"team_box_capacity": read_push_int(
				pc_to_instruction,
				DIALOG_PC_MAP["team_box_capacity"][0],
			),
			"low_battery_controller_index": read_push_int(
				pc_to_instruction,
				DIALOG_PC_MAP["low_battery_controller_index"],
			),
			"register_message_balloon_slot": read_push_int(
				pc_to_instruction,
				DIALOG_PC_MAP["register_message_balloon_slot"],
			),
		},
		"waza_desc_fields": read_int_list(
			pc_to_instruction,
			WAZA_DESC_FIELD_PCS,
		),
		"tables": {
			"random_weight_triples": [
				read_int_list(pc_to_instruction, group)
				for group in RANDOM_WEIGHT_TRIPLE_PCS
			],
			"random_weight_outputs": {
				key: read_push_int(pc_to_instruction, pc)
				for key, pc in RANDOM_WEIGHT_OUTPUT_PCS.items()
			},
			"stage_value_table_a": read_int_list(
				pc_to_instruction,
				STAGE_VALUE_TABLE_A_PCS,
			),
			"stage_value_table_b": read_int_list(
				pc_to_instruction,
				STAGE_VALUE_TABLE_B_PCS,
			),
		},
	}

	return semantic


def apply_semantic(document, semantic):
	if semantic.get("format") != FORMAT_VERSION:
		raise ValueError(
			f"Unsupported JSON format: {semantic.get('format')!r}"
		)

	pc_to_instruction, pc_to_string_index = build_pc_maps(document)
	string_entries = document["string_section"]["entries"]

	newitem_sequence = semantic["embedded_scripts"]["newitem_sequence"]
	write_push_str_group(
		string_entries,
		pc_to_string_index,
		NEWITEM_STRING_PCS,
		newitem_sequence,
	)
	write_push_str_group(
		string_entries,
		pc_to_string_index,
		[MODEL_BRRES_PC],
		[semantic["embedded_scripts"]["model_brres"]],
	)
	write_push_str_group(
		string_entries,
		pc_to_string_index,
		[FALL_MOTION_PC],
		[semantic["embedded_scripts"]["fall_motion"]],
	)

	write_push_int(
		pc_to_instruction,
		MESSAGE_PC_MAP["dynamic_category_base_high"],
		semantic["messages"]["dynamic_category_base_high"],
	)
	write_push_int(
		pc_to_instruction,
		MESSAGE_PC_MAP["dynamic_category_base_low"],
		semantic["messages"]["dynamic_category_base_low"],
	)
	write_int_list(
		pc_to_instruction,
		MESSAGE_PC_MAP["difficulty_rank_labels"],
		semantic["messages"]["difficulty_rank_labels"],
		"messages.difficulty_rank_labels",
	)
	write_push_int(
		pc_to_instruction,
		MESSAGE_PC_MAP["team_box_full"],
		semantic["messages"]["team_box_full"],
	)
	write_push_int(
		pc_to_instruction,
		MESSAGE_PC_MAP["low_battery"],
		semantic["messages"]["low_battery"],
	)
	write_push_int(
		pc_to_instruction,
		MESSAGE_PC_MAP["init_balloon"],
		semantic["messages"]["init_balloon"],
	)
	multiplayer_prompt = semantic["messages"].get("multiplayer_prompt")
	if read_variant(document) == "multi":
		if multiplayer_prompt is None:
			raise ValueError("messages.multiplayer_prompt cannot be null for this file")
		write_push_int(
			pc_to_instruction,
			MESSAGE_PC_MAP["multiplayer_prompt"],
			multiplayer_prompt,
		)

	write_push_int(
		pc_to_instruction,
		FLAG_PC_MAP["availability_flag"],
		semantic["flags"]["availability_flag"],
	)
	write_push_int(
		pc_to_instruction,
		FLAG_PC_MAP["open_gate_flag"],
		semantic["flags"]["open_gate_flag"],
	)
	for pc in FLAG_PC_MAP["fall_animation_flag"]:
		write_push_int(
			pc_to_instruction,
			pc,
			semantic["flags"]["fall_animation_flag"],
		)

	for pc in DIALOG_PC_MAP["team_box_capacity"]:
		write_push_int(
			pc_to_instruction,
			pc,
			semantic["dialogs"]["team_box_capacity"],
		)
	write_push_int(
		pc_to_instruction,
		DIALOG_PC_MAP["low_battery_controller_index"],
		semantic["dialogs"]["low_battery_controller_index"],
	)
	write_push_int(
		pc_to_instruction,
		DIALOG_PC_MAP["register_message_balloon_slot"],
		semantic["dialogs"]["register_message_balloon_slot"],
	)

	write_int_list(
		pc_to_instruction,
		WAZA_DESC_FIELD_PCS,
		semantic["waza_desc_fields"],
		"waza_desc_fields",
	)

	random_weight_triples = semantic["tables"]["random_weight_triples"]
	if len(random_weight_triples) != len(RANDOM_WEIGHT_TRIPLE_PCS):
		raise ValueError(
			f"tables.random_weight_triples must contain {len(RANDOM_WEIGHT_TRIPLE_PCS)} rows"
		)
	for group_pcs, row in zip(RANDOM_WEIGHT_TRIPLE_PCS, random_weight_triples):
		write_int_list(
			pc_to_instruction,
			group_pcs,
			row,
			"tables.random_weight_triples row",
		)

	for key, pc in RANDOM_WEIGHT_OUTPUT_PCS.items():
		write_push_int(
			pc_to_instruction,
			pc,
			semantic["tables"]["random_weight_outputs"][key],
		)

	write_int_list(
		pc_to_instruction,
		STAGE_VALUE_TABLE_A_PCS,
		semantic["tables"]["stage_value_table_a"],
		"tables.stage_value_table_a",
	)
	write_int_list(
		pc_to_instruction,
		STAGE_VALUE_TABLE_B_PCS,
		semantic["tables"]["stage_value_table_b"],
		"tables.stage_value_table_b",
	)

	return document


def default_json_output(input_path):
	return basename_without_ext(input_path) + ".json"


def default_pkc_output(original_pkc_path):
	base, ext = os.path.splitext(original_pkc_path)
	return base + ".rebuilt" + ext


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
				print(
					f"First difference at 0x{index:X}: original=0x{left:02X}, rebuilt=0x{right:02X}",
					file=sys.stderr,
				)
				break
		if len(original) != len(rebuilt):
			print(
				f"Length differs: original={len(original)} rebuilt={len(rebuilt)}",
				file=sys.stderr,
			)
		raise SystemExit(1)
	print("Roundtrip OK")


def command_summary(args):
	document = decode_pkc(args.input)
	semantic = export_semantic(document)
	print(f"Variant: {semantic['variant']}")
	print(f"Embedded NewItem scripts: {len(semantic['embedded_scripts']['newitem_sequence'])}")
	print(f"Model: {semantic['embedded_scripts']['model_brres']}")
	print(f"Fall motion: {semantic['embedded_scripts']['fall_motion']}")
	print(f"Init balloon MID: {semantic['messages']['init_balloon']}")
	print(f"Multiplayer prompt MID: {semantic['messages']['multiplayer_prompt']}")


def build_parser():
	parser = argparse.ArgumentParser(
		description="Export and rebuild the semantic editable parts of CItem_collection/CItem_multi PKC files."
	)
	subparsers = parser.add_subparsers(dest="command", required=True)

	summary_parser = subparsers.add_parser("summary", help="Show a short semantic summary.")
	summary_parser.add_argument("input", help="Input PKC file")
	summary_parser.set_defaults(func=command_summary)

	export_parser = subparsers.add_parser("export", help="Export to editable JSON.")
	export_parser.add_argument("input", help="Input PKC file")
	export_parser.add_argument("output", nargs="?", help="Output JSON file")
	export_parser.set_defaults(func=command_export)

	import_parser = subparsers.add_parser("import", help="Import edited JSON and rebuild PKC.")
	import_parser.add_argument("input", help="Input JSON file")
	import_parser.add_argument("original_pkc", help="Original PKC file")
	import_parser.add_argument("output", nargs="?", help="Output PKC file")
	import_parser.set_defaults(func=command_import)

	verify_parser = subparsers.add_parser("verify", help="Semantic no-edit roundtrip check.")
	verify_parser.add_argument("input", help="Input PKC file")
	verify_parser.set_defaults(func=command_verify)

	return parser


def main():
	parser = build_parser()
	args = parser.parse_args()
	args.func(args)


if __name__ == "__main__":
	main()
