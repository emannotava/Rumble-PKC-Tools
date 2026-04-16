#!/usr/bin/env python3
import argparse
import json
import os
import sys

try:
	from pyPKCToolBase import decode_pkc, encode_pkc_document
except ImportError as exc:
	print(f"Failed to import pyPKCToolBase.py: {exc}", file=sys.stderr)
	sys.exit(1)


TOOL_DESCRIPTION = "Edit CItem_ticket.pkc via semantic JSON."
FORMAT_VERSION = "citem-ticket-semantic-json-1"

COMMON_NEWITEM_STRING_PCS = [
	0x0394, 0x039A, 0x03A0, 0x03A6, 0x03AC, 0x03B2, 0x03B8, 0x03BE,
	0x03C4, 0x03CA, 0x03D0, 0x03D6, 0x03DC, 0x03E2, 0x03E8, 0x03EE,
	0x040A, 0x075F,
]

COMMON_MESSAGE_PC_MAP = {
	"dynamic_category_base_high": 0x056A,
	"dynamic_category_base_low": 0x0575,
	"difficulty_rank_labels": [0x0858, 0x085D, 0x0862, 0x086E, 0x0873, 0x0878, 0x087D, 0x0882],
}

ENERGY_TIERS = [
	{"threshold_pc": 0x0971, "model_pc": 0x0975, "loop_pc": 0x0979},
	{"threshold_pc": 0x0980, "model_pc": 0x0984, "loop_pc": 0x0988},
	{"threshold_pc": 0x098D, "model_pc": 0x0991, "loop_pc": 0x0995},
	{"threshold_pc": None, "model_pc": 0x099A, "loop_pc": 0x099E},
]

TEXT_TABLE_CONFIG = {
	"ticket_name": {"min_pc": 0x09A9, "max_pc": 0x09AC, "base_pc": 0x09B2, "index_subtract_pc": 0x09B5},
	"ticket_description": {"min_pc": 0x09C0, "max_pc": 0x09C3, "base_pc": 0x09C9, "index_subtract_pc": 0x09CC},
	"move_name": {"min_pc": 0x09D7, "max_pc": 0x09DA, "base_pc": 0x09E0, "index_subtract_pc": 0x09E3},
	"move_description": {"min_pc": 0x09EE, "max_pc": 0x09F1, "base_pc": 0x09F7, "index_subtract_pc": 0x09FA},
}

ITEM_MODEL_PC = 0x0A19
WORLD_LABEL_CATEGORY_PC = 0x0A8D
WORLD_LABEL_MID_PC = 0x0A8E
WORLD_LABEL_SOUND_PC = 0x0A97
HOVER_OFFSET_START_PC = 0x0A1C
HOVER_OFFSET_STEP_PC = 0x0A33
POST_LABEL_STEP_PC = 0x0A9A
LANDING_SOUND_PC = 0x0A3E


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


def read_push_float(pc_to_instruction, pc):
	instruction = expect_instruction(pc_to_instruction, pc, "push_s_f")
	return float(instruction.get("decoded", {}).get("value"))


def write_push_float(pc_to_instruction, pc, value):
	instruction = expect_instruction(pc_to_instruction, pc, "push_s_f")
	float_value = float(value)
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
			"newitem_sequence": read_string_list(string_entries, pc_to_string_index, COMMON_NEWITEM_STRING_PCS),
		},
		"common_messages": {
			"dynamic_category_base_high": read_push_int(pc_to_instruction, COMMON_MESSAGE_PC_MAP["dynamic_category_base_high"]),
			"dynamic_category_base_low": read_push_int(pc_to_instruction, COMMON_MESSAGE_PC_MAP["dynamic_category_base_low"]),
			"difficulty_rank_labels": read_int_list(pc_to_instruction, COMMON_MESSAGE_PC_MAP["difficulty_rank_labels"]),
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
		"text_tables": {
			name: {
				"min_inclusive": read_push_int(pc_to_instruction, config["min_pc"]),
				"max_inclusive": read_push_int(pc_to_instruction, config["max_pc"]),
				"base_mid": read_push_int(pc_to_instruction, config["base_pc"]),
				"index_subtract": read_push_int(pc_to_instruction, config["index_subtract_pc"]),
			}
			for name, config in TEXT_TABLE_CONFIG.items()
		},
		"item_visuals": {
			"item_model_brres": read_push_str(string_entries, pc_to_string_index, ITEM_MODEL_PC)[0],
			"world_label_category_id": read_push_int(pc_to_instruction, WORLD_LABEL_CATEGORY_PC),
			"world_label_mid": read_push_int(pc_to_instruction, WORLD_LABEL_MID_PC),
			"world_label_sound_se": read_push_int(pc_to_instruction, WORLD_LABEL_SOUND_PC),
			"hover_offset_start": read_push_float(pc_to_instruction, HOVER_OFFSET_START_PC),
			"hover_offset_step": read_push_float(pc_to_instruction, HOVER_OFFSET_STEP_PC),
			"post_label_step": read_push_float(pc_to_instruction, POST_LABEL_STEP_PC),
			"landing_sound_se": read_push_int(pc_to_instruction, LANDING_SOUND_PC),
		},
	}


def apply_semantic(document, semantic):
	if semantic.get("format") != FORMAT_VERSION:
		raise ValueError(f"Unsupported JSON format: {semantic.get('format')!r}")
	pc_to_instruction, pc_to_string_index = build_pc_maps(document)
	string_entries = document["string_section"]["entries"]
	write_push_str_group(string_entries, pc_to_string_index, COMMON_NEWITEM_STRING_PCS, semantic["embedded_scripts"]["newitem_sequence"])
	write_push_int(pc_to_instruction, COMMON_MESSAGE_PC_MAP["dynamic_category_base_high"], semantic["common_messages"]["dynamic_category_base_high"])
	write_push_int(pc_to_instruction, COMMON_MESSAGE_PC_MAP["dynamic_category_base_low"], semantic["common_messages"]["dynamic_category_base_low"])
	write_int_list(pc_to_instruction, COMMON_MESSAGE_PC_MAP["difficulty_rank_labels"], semantic["common_messages"]["difficulty_rank_labels"], "common_messages.difficulty_rank_labels")
	for name, config in TEXT_TABLE_CONFIG.items():
		entry = semantic["text_tables"][name]
		write_push_int(pc_to_instruction, config["min_pc"], entry["min_inclusive"])
		write_push_int(pc_to_instruction, config["max_pc"], entry["max_inclusive"])
		write_push_int(pc_to_instruction, config["base_pc"], entry["base_mid"])
		write_push_int(pc_to_instruction, config["index_subtract_pc"], entry["index_subtract"])
	tiers = semantic["energy_visuals"]["tiers"]
	if len(tiers) != len(ENERGY_TIERS):
		raise ValueError(f"energy_visuals.tiers must contain {len(ENERGY_TIERS)} rows")
	for config, entry in zip(ENERGY_TIERS, tiers):
		if config["threshold_pc"] is not None:
			write_push_int(pc_to_instruction, config["threshold_pc"], entry["threshold_lt"])
		write_push_str_group(string_entries, pc_to_string_index, [config["model_pc"]], [entry["model_brres"]])
		write_push_str_group(string_entries, pc_to_string_index, [config["loop_pc"]], [entry["loop_motion"]])
	write_push_str_group(string_entries, pc_to_string_index, [ITEM_MODEL_PC], [semantic["item_visuals"]["item_model_brres"]])
	write_push_int(pc_to_instruction, WORLD_LABEL_CATEGORY_PC, semantic["item_visuals"]["world_label_category_id"])
	write_push_int(pc_to_instruction, WORLD_LABEL_MID_PC, semantic["item_visuals"]["world_label_mid"])
	write_push_int(pc_to_instruction, WORLD_LABEL_SOUND_PC, semantic["item_visuals"]["world_label_sound_se"])
	write_push_float(pc_to_instruction, HOVER_OFFSET_START_PC, semantic["item_visuals"]["hover_offset_start"])
	write_push_float(pc_to_instruction, HOVER_OFFSET_STEP_PC, semantic["item_visuals"]["hover_offset_step"])
	write_push_float(pc_to_instruction, POST_LABEL_STEP_PC, semantic["item_visuals"]["post_label_step"])
	write_push_int(pc_to_instruction, LANDING_SOUND_PC, semantic["item_visuals"]["landing_sound_se"])
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
	rebuilt_document = apply_semantic(decode_pkc(args.input), semantic)
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
