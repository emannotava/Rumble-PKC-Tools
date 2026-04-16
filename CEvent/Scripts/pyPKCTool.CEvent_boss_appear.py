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


FORMAT_VERSION = "cevent-boss-appear-semantic-json-1"
COMMON_NEWITEM_STRING_PCS = [
	0x0394, 0x039A, 0x03A0, 0x03A6, 0x03AC, 0x03B2, 0x03B8, 0x03BE,
	0x03C4, 0x03CA, 0x03D0, 0x03D6, 0x03DC, 0x03E2, 0x03E8, 0x03EE,
	0x040A,
]
MONEY_ITEM_SCRIPT_PC = 0x075F
CAMERA_FLOAT_PCS = {"height_scale": 0x0967, "distance_scale": 0x096A, "angle_deg": 0x096D}
NAME_LAYOUT_STRING_PCS = {"archive_path": 0x0986, "layout_name": 0x0987, "main_name_slot": 0x098B, "caption_slot": 0x0992, "shadow_slot_1": 0x0999, "shadow_slot_2": 0x09A0, "state_normal": 0x09A7}
SCENE_ASSET_STRING_PCS = {"background_model_brres": 0x09ED, "background_motion": 0x09F4, "dummy_script": 0x09F7, "dummy_model_brres": 0x0A01, "background_loop_motion": 0x0A0A, "dummy_appear_motion": 0x0A0E, "effect_motion": 0x0A29, "hp_archive_path": 0x0A35, "hp_layout_name": 0x0A36, "hp_state_normal": 0x0A3A, "replay_motion_a": 0x0AFE, "replay_motion_b": 0x0B56}
SCENE_INT_PCS = {"background_play_id": 0x0A04, "background_loop_flag": 0x0A07, "dummy_scene_motion_id": 0x0A18, "spawn_se_id": 0x0A20, "effect_a_id": 0x0A24, "effect_a_dir_deg": 0x0A25, "effect_b_id": 0x0A31, "effect_b_dir_deg": 0x0A32, "intro_bgm_id": 0x0AC1, "loop_bgm_id": 0x0AC6, "secondary_motion_id": 0x0B16, "boss_hide_hp_value": 0x0B26}
SCENE_FLOAT_PCS = {"reveal_model_scale": 0x0B09}
HP_BASE_SCALE_PC = 0x0A3E
HP_TIER_PCS = [
	{"threshold_pc": 0x0A45, "range_start_pc": 0x0A49, "range_end_pc": 0x0A4A, "scale_start_pc": 0x0A4B, "scale_end_pc": 0x0A4D},
	{"threshold_pc": 0x0A53, "range_start_pc": 0x0A57, "range_end_pc": 0x0A58, "scale_start_pc": 0x0A59, "scale_end_pc": 0x0A5B},
	{"threshold_pc": 0x0A61, "range_start_pc": 0x0A65, "range_end_pc": 0x0A66, "scale_start_pc": 0x0A67, "scale_end_pc": 0x0A69},
	{"threshold_pc": 0x0A6F, "range_start_pc": 0x0A73, "range_end_pc": 0x0A74, "scale_start_pc": 0x0A75, "scale_end_pc": 0x0A77},
	{"threshold_pc": 0x0A7D, "range_start_pc": 0x0A81, "range_end_pc": 0x0A82, "scale_start_pc": 0x0A83, "scale_end_pc": 0x0A85},
	{"threshold_pc": 0x0A8B, "range_start_pc": 0x0A8F, "range_end_pc": 0x0A90, "scale_start_pc": 0x0A91, "scale_end_pc": 0x0A93},
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


def export_semantic(document):
	pc_to_instruction, pc_to_string_index = build_pc_maps(document)
	string_entries = document["string_section"]["entries"]
	tiers=[]
	for config in HP_TIER_PCS:
		tiers.append({"threshold_gt": read_push_int(pc_to_instruction, config["threshold_pc"]), "range_start": read_push_int(pc_to_instruction, config["range_start_pc"]), "range_end": read_push_int(pc_to_instruction, config["range_end_pc"]), "scale_start": read_push_float(pc_to_instruction, config["scale_start_pc"]), "scale_end": read_push_float(pc_to_instruction, config["scale_end_pc"])})
	return {"format": FORMAT_VERSION, "source_basename": document.get("source_basename"),
		"embedded_scripts": {"linked_event_scripts": read_string_list(string_entries, pc_to_string_index, COMMON_NEWITEM_STRING_PCS), "money_item_script": read_push_str(string_entries, pc_to_string_index, MONEY_ITEM_SCRIPT_PC)[0]},
		"camera_setup": {key: read_push_float(pc_to_instruction, pc) for key, pc in CAMERA_FLOAT_PCS.items()},
		"name_layout": {key: read_push_str(string_entries, pc_to_string_index, pc)[0] for key, pc in NAME_LAYOUT_STRING_PCS.items()},
		"scene_assets": {key: read_push_str(string_entries, pc_to_string_index, pc)[0] for key, pc in SCENE_ASSET_STRING_PCS.items()},
		"scene_values": {"integers": {key: read_push_int(pc_to_instruction, pc) for key, pc in SCENE_INT_PCS.items()}, "floats": {key: read_push_float(pc_to_instruction, pc) for key, pc in SCENE_FLOAT_PCS.items()}},
		"hp_increase_layout": {"base_scale": read_push_float(pc_to_instruction, HP_BASE_SCALE_PC), "tiers": tiers},
	}


def apply_semantic(document, semantic):
	if semantic.get("format") != FORMAT_VERSION:
		raise ValueError(f"Unsupported JSON format: {semantic.get('format')!r}")
	pc_to_instruction, pc_to_string_index = build_pc_maps(document)
	string_entries = document["string_section"]["entries"]
	write_push_str_group(string_entries, pc_to_string_index, COMMON_NEWITEM_STRING_PCS, semantic["embedded_scripts"]["linked_event_scripts"])
	write_push_str_group(string_entries, pc_to_string_index, [MONEY_ITEM_SCRIPT_PC], [semantic["embedded_scripts"]["money_item_script"]])
	for key, pc in CAMERA_FLOAT_PCS.items():
		write_push_float(pc_to_instruction, pc, semantic["camera_setup"][key])
	for key, pc in NAME_LAYOUT_STRING_PCS.items():
		write_push_str_group(string_entries, pc_to_string_index, [pc], [semantic["name_layout"][key]])
	for key, pc in SCENE_ASSET_STRING_PCS.items():
		write_push_str_group(string_entries, pc_to_string_index, [pc], [semantic["scene_assets"][key]])
	for key, pc in SCENE_INT_PCS.items():
		write_push_int(pc_to_instruction, pc, semantic["scene_values"]["integers"][key])
	for key, pc in SCENE_FLOAT_PCS.items():
		write_push_float(pc_to_instruction, pc, semantic["scene_values"]["floats"][key])
	write_push_float(pc_to_instruction, HP_BASE_SCALE_PC, semantic["hp_increase_layout"]["base_scale"])
	tiers=semantic["hp_increase_layout"]["tiers"]
	if len(tiers)!=len(HP_TIER_PCS):
		raise ValueError(f"hp_increase_layout.tiers must contain {len(HP_TIER_PCS)} entries")
	for config, entry in zip(HP_TIER_PCS, tiers):
		write_push_int(pc_to_instruction, config["threshold_pc"], entry["threshold_gt"])
		write_push_int(pc_to_instruction, config["range_start_pc"], entry["range_start"])
		write_push_int(pc_to_instruction, config["range_end_pc"], entry["range_end"])
		write_push_float(pc_to_instruction, config["scale_start_pc"], entry["scale_start"])
		write_push_float(pc_to_instruction, config["scale_end_pc"], entry["scale_end"])
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


def build_parser():
	parser = argparse.ArgumentParser(description="Edit CEvent_boss_appear.pkc via semantic JSON.")
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
	parser = build_parser()
	args = parser.parse_args()
	args.func(args)


if __name__ == "__main__":
	main()
