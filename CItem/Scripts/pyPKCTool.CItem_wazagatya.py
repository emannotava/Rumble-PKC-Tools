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


TOOL_DESCRIPTION = "Edit CItem_wazagatya.pkc via semantic JSON."
FORMAT_VERSION = "citem-wazagatya-semantic-json-1"

COMMON_NEWITEM_STRING_PCS = [
	0x0394, 0x039A, 0x03A0, 0x03A6, 0x03AC, 0x03B2, 0x03B8, 0x03BE,
	0x03C4, 0x03CA, 0x03D0, 0x03D6, 0x03DC, 0x03E2, 0x03E8, 0x03EE,
	0x040A, 0x075F,
]

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
	"loop_motion": 0x176D,
	"fall_motion": 0x1883,
	"normal_motion": 0x1959,
	"request_motion_after_normal": 0x195D,
}

WAZA_DESC_FIELD_PCS = {
	"preview_fields": [0x08A6, 0x08AA, 0x08AE, 0x08B2, 0x08B6],
	"detail_fields": [0x1068, 0x106C, 0x1070],
	"entry_name_field": 0x1817,
	"entry_type_field": 0x1835,
	"entry_type_aux_field": 0x183B,
	"classification_field": 0x1906,
	"move_name_field": 0x196F,
	"move_description_field": 0x1982,
}

MESSAGE_MID_PCS = {
	"menu_message_mids": [0x17E6, 0x17EB, 0x17F0, 0x17F5, 0x17FA],
	"entry_name_mid": 0x1813,
	"entry_type_mid": 0x1831,
	"yesno_prompt_mid": 0x184F,
	"yesno_followup_mid": 0x185B,
	"init_balloon_mid": 0x1867,
	"species_message_mid": 0x1893,
	"purchase_prompt_normal_mid": 0x18AE,
	"purchase_prompt_alt_mid": 0x18B6,
	"move_name_mid": 0x196B,
	"move_description_mid": 0x197E,
	"result_message_a_mid": 0x1992,
	"result_message_b_mid": 0x1995,
	"result_message_c_mid": 0x19C7,
	"result_message_d_mid": 0x19E1,
	"cancel_message_mid": 0x19FB,
}

GAMEPLAY_PCS = {
	"availability_flag": 0x1873,
	"purchase_stat_id": 0x18DC,
	"money_stat_id": 0x18E0,
	"purchase_retry_limit": 0x18E9,
	"reroll_retry_limit": 0x18F4,
	"classification_scale": 0x1901,
	"show_balloon_off": 0x1950,
	"show_balloon_on": 0x1964,
	"purchase_success_se": 0x1955,
	"reveal_se": 0x1967,
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
			"loop_motion": read_push_str(string_entries, pc_to_string_index, TERMINAL_VISUAL_PCS["loop_motion"])[0],
			"fall_motion": read_push_str(string_entries, pc_to_string_index, TERMINAL_VISUAL_PCS["fall_motion"])[0],
			"normal_motion": read_push_str(string_entries, pc_to_string_index, TERMINAL_VISUAL_PCS["normal_motion"])[0],
			"request_motion_after_normal": read_push_str(string_entries, pc_to_string_index, TERMINAL_VISUAL_PCS["request_motion_after_normal"])[0],
		},
		"waza_desc_fields": {
			"preview_fields": read_int_list(pc_to_instruction, WAZA_DESC_FIELD_PCS["preview_fields"]),
			"detail_fields": read_int_list(pc_to_instruction, WAZA_DESC_FIELD_PCS["detail_fields"]),
			"entry_name_field": read_push_int(pc_to_instruction, WAZA_DESC_FIELD_PCS["entry_name_field"]),
			"entry_type_field": read_push_int(pc_to_instruction, WAZA_DESC_FIELD_PCS["entry_type_field"]),
			"entry_type_aux_field": read_push_int(pc_to_instruction, WAZA_DESC_FIELD_PCS["entry_type_aux_field"]),
			"classification_field": read_push_int(pc_to_instruction, WAZA_DESC_FIELD_PCS["classification_field"]),
			"move_name_field": read_push_int(pc_to_instruction, WAZA_DESC_FIELD_PCS["move_name_field"]),
			"move_description_field": read_push_int(pc_to_instruction, WAZA_DESC_FIELD_PCS["move_description_field"]),
		},
		"message_mids": {
			name: (read_int_list(pc_to_instruction, pcs) if isinstance(pcs, list) else read_push_int(pc_to_instruction, pcs))
			for name, pcs in MESSAGE_MID_PCS.items()
		},
		"gameplay": {
			"availability_flag": read_push_int(pc_to_instruction, GAMEPLAY_PCS["availability_flag"]),
			"purchase_stat_id": read_push_int(pc_to_instruction, GAMEPLAY_PCS["purchase_stat_id"]),
			"money_stat_id": read_push_int(pc_to_instruction, GAMEPLAY_PCS["money_stat_id"]),
			"purchase_retry_limit": read_push_int(pc_to_instruction, GAMEPLAY_PCS["purchase_retry_limit"]),
			"reroll_retry_limit": read_push_int(pc_to_instruction, GAMEPLAY_PCS["reroll_retry_limit"]),
			"classification_scale": read_push_float(pc_to_instruction, GAMEPLAY_PCS["classification_scale"]),
			"show_balloon_off": read_push_int(pc_to_instruction, GAMEPLAY_PCS["show_balloon_off"]),
			"show_balloon_on": read_push_int(pc_to_instruction, GAMEPLAY_PCS["show_balloon_on"]),
			"purchase_success_se": read_push_int(pc_to_instruction, GAMEPLAY_PCS["purchase_success_se"]),
			"reveal_se": read_push_int(pc_to_instruction, GAMEPLAY_PCS["reveal_se"]),
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
	for key in ("model_brres", "loop_motion", "fall_motion", "normal_motion", "request_motion_after_normal"):
		write_push_str_group(string_entries, pc_to_string_index, [TERMINAL_VISUAL_PCS[key]], [semantic["terminal_visuals"][key]])
	write_int_list(pc_to_instruction, WAZA_DESC_FIELD_PCS["preview_fields"], semantic["waza_desc_fields"]["preview_fields"], "waza_desc_fields.preview_fields")
	write_int_list(pc_to_instruction, WAZA_DESC_FIELD_PCS["detail_fields"], semantic["waza_desc_fields"]["detail_fields"], "waza_desc_fields.detail_fields")
	for key in ("entry_name_field", "entry_type_field", "entry_type_aux_field", "classification_field", "move_name_field", "move_description_field"):
		write_push_int(pc_to_instruction, WAZA_DESC_FIELD_PCS[key], semantic["waza_desc_fields"][key])
	for name, pcs in MESSAGE_MID_PCS.items():
		if isinstance(pcs, list):
			write_int_list(pc_to_instruction, pcs, semantic["message_mids"][name], f"message_mids.{name}")
		else:
			write_push_int(pc_to_instruction, pcs, semantic["message_mids"][name])
	for key in ("availability_flag", "purchase_stat_id", "money_stat_id", "purchase_retry_limit", "reroll_retry_limit", "show_balloon_off", "show_balloon_on", "purchase_success_se", "reveal_se"):
		write_push_int(pc_to_instruction, GAMEPLAY_PCS[key], semantic["gameplay"][key])
	write_push_float(pc_to_instruction, GAMEPLAY_PCS["classification_scale"], semantic["gameplay"]["classification_scale"])
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
