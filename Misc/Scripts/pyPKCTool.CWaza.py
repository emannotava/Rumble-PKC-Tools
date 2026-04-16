#!/usr/bin/env python3
import argparse
import copy
import json
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
	sys.path.insert(0, SCRIPT_DIR)

try:
	import pyPKCToolBase as base
except ImportError as exc:
	print(f"Failed to import pyPKCToolBase.py: {exc}", file=sys.stderr)
	sys.exit(1)


try:
	from pyPKCEnumLib import MoveEnum
except ImportError as exc:
	print(f"Failed to import pyPKCEnumLib.py: {exc}", file=sys.stderr)
	sys.exit(1)


TOOL_DESCRIPTION = 'Edit NCWZ.pkc via semantic JSON.'
FORMAT_VERSION = 'ncwz-semantic-json-1'

EVENT_SCRIPT_PCS = {'prologue': 916, 'prologue2': 922, 'boss_appear': 928, 'boss_die': 934, 'clear': 940, 'battleroyal_clear': 946, 'lose': 952, 'warp': 958, 'warppoint_open': 964, 'battleroyal_open': 970, 'nextgrade_open': 976, 'door_open': 982, 'guest_appear': 988, 'battleroyal_begin': 994, 'battleroyal_defeat': 1000, 'battleroyal_prize': 1006, 'battleroyal_begin_repeat': 1034}
MONEY_ITEM_PC = 0x075F
STRING_FIELDS = {'embedded_scripts': {'follower_helper_script': 4689}, 'follower_behavior': {'attachment_node': 4710}}
INT_FIELDS = {'common_messages': {'dynamic_category_base_high': 1386, 'dynamic_category_base_low': 1397}, 'common_field_indexes': {'collection_type_field': 181, 'attack_field': 2795, 'boss_attack_multiplier_field': 2817, 'defense_field': 2907, 'boss_defense_multiplier_field': 2929, 'boss_speed_override_field': 2955, 'speed_field': 2965, 'boss_scalar_field': 3007, 'hp_field': 3164, 'boss_hp_multiplier_field': 3183, 'level_formula_hp_field': 3333}, 'follower_behavior': {'spawn_sound_se': 4695, 'spawn_effect_id': 4718, 'spawn_effect_dir_deg': 4719, 'voice_fallback_waza_desc_field': 5009}}
INT_LIST_FIELDS = {'common_messages': {'difficulty_rank_labels': [2136, 2141, 2146, 2158, 2163, 2168, 2173, 2178]}, 'common_field_indexes': {'waza_desc_preview_fields': [2214, 2218, 2222, 2226, 2230], 'ppd_register_slots': [2315, 2334, 2364, 2383, 2413, 2432, 2462, 2481, 2500, 2519, 2540, 2561, 2582, 2603]}, 'follower_behavior': {'detail_waza_desc_fields': [4264, 4271, 4275, 4279, 4283, 4287, 4291, 4295]}}
FLOAT_FIELDS = {'follower_behavior': {'register_angle_offset_deg': 4505, 'register_blend_base_deg': 5766}}
FLOAT_LIST_FIELDS = {}

MOVE_ID_TO_SYMBOL = {int(member): member.name for member in MoveEnum}
MOVE_SYMBOL_TO_ID = {member.name: int(member) for member in MoveEnum}



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
	base_path, ext = os.path.splitext(original_pkc_path)
	return base_path + ".rebuilt" + ext


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


def signed24_to_u24(value):
	integer = int(value)
	if not -(1 << 23) <= integer < (1 << 23):
		raise ValueError(f"Value out of signed 24-bit range: {integer}")
	return integer & 0xFFFFFF


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


def read_push_float(pc_to_instruction, pc):
	instruction = expect_instruction(pc_to_instruction, pc, "push_s_f")
	decoded = instruction.get("decoded", {})
	return float(decoded.get("value"))


def write_push_float(pc_to_instruction, pc, value):
	instruction = expect_instruction(pc_to_instruction, pc, "push_s_f")
	float_value = float(value)
	instruction["decoded"] = {
		"type": "float",
		"value": float_value,
		"source_text": repr(float_value),
		"bit_pattern_hex": None,
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
			raise ValueError(f"Conflicting values for shared string index {index}: {index_to_value[index]!r} vs {value!r}")
		index_to_value[index] = value
	for index, value in index_to_value.items():
		string_entries[index] = str(value)


def read_int_list(pc_to_instruction, pcs):
	return [read_push_int(pc_to_instruction, pc) for pc in pcs]


def write_int_list(pc_to_instruction, pcs, values, label):
	if len(values) != len(pcs):
		raise ValueError(f"{label} must contain {len(pcs)} values, got {len(values)}")
	for pc, value in zip(pcs, values):
		write_push_int(pc_to_instruction, pc, value)


def move_symbol(value):
	value = int(value)
	return MOVE_ID_TO_SYMBOL.get(value, f"MOVE_{value:04d}")


def move_value_to_id(value):
	if isinstance(value, int):
		return value
	if isinstance(value, str):
		text = value.strip()
		if text in MOVE_SYMBOL_TO_ID:
			return MOVE_SYMBOL_TO_ID[text]
		if text.startswith("MOVE_"):
			for part in text.split("_")[1:]:
				if part.isdigit():
					return int(part)
			return int(text[5:])
		return int(text)
	raise ValueError(f"Invalid move value: {value!r}")



def export_semantic(document):
	pc_to_instruction, pc_to_string_index = build_pc_maps(document)
	string_entries = document["string_section"]["entries"]
	semantic = {
		"format": FORMAT_VERSION,
		"source_basename": document.get("source_basename"),
		"embedded_scripts": {
			"event_scripts": {name: read_push_str(string_entries, pc_to_string_index, pc)[0] for name, pc in EVENT_SCRIPT_PCS.items()},
			"money_item_script": read_push_str(string_entries, pc_to_string_index, MONEY_ITEM_PC)[0],
		},
	}

	for section_name, mapping in STRING_FIELDS.items():
		semantic.setdefault(section_name, {})
		for field_name, pc in mapping.items():
			semantic[section_name][field_name] = read_push_str(string_entries, pc_to_string_index, pc)[0]
	for section_name, mapping in INT_FIELDS.items():
		semantic.setdefault(section_name, {})
		for field_name, pc in mapping.items():
			semantic[section_name][field_name] = read_push_int(pc_to_instruction, pc)
	for section_name, mapping in INT_LIST_FIELDS.items():
		semantic.setdefault(section_name, {})
		for field_name, pcs in mapping.items():
			semantic[section_name][field_name] = read_int_list(pc_to_instruction, pcs)
	for section_name, mapping in FLOAT_FIELDS.items():
		semantic.setdefault(section_name, {})
		for field_name, pc in mapping.items():
			semantic[section_name][field_name] = read_push_float(pc_to_instruction, pc)
	for section_name, mapping in FLOAT_LIST_FIELDS.items():
		semantic.setdefault(section_name, {})
		for field_name, pcs in mapping.items():
			semantic[section_name][field_name] = [read_push_float(pc_to_instruction, pc) for pc in pcs]

	semantic.setdefault("follower_behavior", {})
	semantic["follower_behavior"]["pokemon_voice_exception_moves"] = [move_symbol(value) for value in (read_push_int(pc_to_instruction, 0x1377), read_push_int(pc_to_instruction, 0x137B), read_push_int(pc_to_instruction, 0x137F), read_push_int(pc_to_instruction, 0x1383))]

	return semantic


def apply_semantic(document, semantic):
	if semantic.get("format") != FORMAT_VERSION:
		raise ValueError(f"Unsupported JSON format: {semantic.get('format')!r}")
	pc_to_instruction, pc_to_string_index = build_pc_maps(document)
	string_entries = document["string_section"]["entries"]
	write_push_str_group(string_entries, pc_to_string_index, list(EVENT_SCRIPT_PCS.values()), [semantic["embedded_scripts"]["event_scripts"][name] for name in EVENT_SCRIPT_PCS])
	write_push_str_group(string_entries, pc_to_string_index, [MONEY_ITEM_PC], [semantic["embedded_scripts"]["money_item_script"]])

	for section_name, mapping in STRING_FIELDS.items():
		for field_name, pc in mapping.items():
			write_push_str_group(string_entries, pc_to_string_index, [pc], [semantic[section_name][field_name]])
	for section_name, mapping in INT_FIELDS.items():
		for field_name, pc in mapping.items():
			write_push_int(pc_to_instruction, pc, semantic[section_name][field_name])
	for section_name, mapping in INT_LIST_FIELDS.items():
		for field_name, pcs in mapping.items():
			write_int_list(pc_to_instruction, pcs, semantic[section_name][field_name], f"{section_name}.{field_name}")
	for section_name, mapping in FLOAT_FIELDS.items():
		for field_name, pc in mapping.items():
			write_push_float(pc_to_instruction, pc, semantic[section_name][field_name])
	for section_name, mapping in FLOAT_LIST_FIELDS.items():
		for field_name, pcs in mapping.items():
			values = semantic[section_name][field_name]
			if len(values) != len(pcs):
				raise ValueError(f"{section_name}.{field_name} must contain {len(pcs)} values, got {len(values)}")
			for pc, value in zip(pcs, values):
				write_push_float(pc_to_instruction, pc, value)

	voice_moves = semantic["follower_behavior"].get("pokemon_voice_exception_moves", [])
	if len(voice_moves) != 4:
		raise ValueError("follower_behavior.pokemon_voice_exception_moves must contain 4 moves")
	for pc, value in zip([0x1377, 0x137B, 0x137F, 0x1383], [move_value_to_id(v) for v in voice_moves]):
		write_push_int(pc_to_instruction, pc, value)

	return document


def command_export(args):
	document = base.decode_pkc(args.input)
	semantic = export_semantic(document)
	output = args.output or default_json_output(args.input)
	dump_json(output, semantic)
	print(f"Wrote {output}")


def command_import(args):
	semantic = load_json(args.input)
	document = base.decode_pkc(args.original_pkc)
	document = apply_semantic(document, semantic)
	output = args.output or default_pkc_output(args.original_pkc)
	with open(output, "wb") as outfile:
		outfile.write(base.encode_pkc_document(document))
	print(f"Wrote {output}")


def command_verify(args):
	document = base.decode_pkc(args.input)
	semantic = export_semantic(document)
	rebuilt_document = apply_semantic(copy.deepcopy(document), semantic)
	rebuilt = base.encode_pkc_document(rebuilt_document)
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
