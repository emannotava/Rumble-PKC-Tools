#!/usr/bin/env python3
import argparse
import json
import os
import struct
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
	sys.path.insert(0, SCRIPT_DIR)

try:
	import pyPKCToolBase as generic
except ImportError as exc:
	print(f"Failed to import pyPKCToolBase.py: {exc}", file=sys.stderr)
	sys.exit(1)

FORMAT_VERSION = 'cdummy-cam-target-semantic-json-1'
TOOL_DESCRIPTION = 'Edit CDummy_cam_target.pkc via semantic JSON.'
SPECS = [{'name': 'embedded_event_scripts',
  'kind': 'string_list',
  'pcs': [916, 922, 928, 934, 940, 946, 952, 958, 964, 970, 976, 982, 988, 994, 1000, 1006, 1034]},
 {'name': 'money_item_script', 'kind': 'string', 'pc': 1887},
 {'name': 'spawn_randomization',
  'kind': 'mixed_dict',
  'entries': [('offset_angle_a', 'float', 1838),
              ('offset_radius_a', 'float', 1841),
              ('facing_angle', 'int', 1844),
              ('speed_min', 'float', 1857),
              ('speed_max', 'float', 1859),
              ('height_min', 'float', 1863),
              ('height_max', 'float', 1865),
              ('dir_min', 'int', 1869),
              ('dir_max', 'int', 1870)]},
 {'name': 'new_obj_register_slots', 'kind': 'int_list', 'pcs': [1875, 1879, 1883]},
 {'name': 'common_tables.random_weight_triples',
  'kind': 'int_matrix',
  'pcs_groups': [[1926, 1928, 1930],
                 [1933, 1935, 1937],
                 [1940, 1942, 1944],
                 [1947, 1949, 1951],
                 [1959, 1961, 1963],
                 [1966, 1968, 1970],
                 [1973, 1975, 1977],
                 [1980, 1982, 1984],
                 [1990, 1992, 1994]]},
 {'name': 'common_tables.random_weight_outputs',
  'kind': 'int_dict',
  'pcs': {'first': 2015, 'second': 2024, 'third': 2027, 'fallback': 2029}},
 {'name': 'common_tables.stage_value_table_a',
  'kind': 'int_list',
  'pcs': [2047, 2049, 2051, 2053, 2059, 2061, 2063, 2065, 2070]},
 {'name': 'common_tables.stage_value_table_b',
  'kind': 'int_list',
  'pcs': [2094, 2096, 2098, 2100, 2106, 2108, 2110, 2112, 2117]},
 {'name': 'difficulty_rank_labels',
  'kind': 'int_list',
  'pcs': [2136, 2141, 2146, 2158, 2163, 2168, 2173, 2178]},
 {'name': 'waza_desc_fields', 'kind': 'int_list', 'pcs': [2214, 2218, 2222, 2226, 2230]},
 {'name': 'selector_sequence_slots',
  'kind': 'int_list',
  'pcs': [2346, 2350, 2354, 2358, 2362, 2366, 2370]},
 {'name': 'pause_flow',
  'kind': 'int_dict',
  'pcs': {'yield_state_a': 2401,
          'controller_index': 2407,
          'alive_required': 2409,
          'selector_slot': 2417,
          'yield_state_b': 2426,
          'callback_slot': 2434,
          'callback_object': 2437}},
 {'name': 'camera_adjustment',
  'kind': 'mixed_dict',
  'entries': [('adjust_factor', 'float', 2856),
              ('state_two_value', 'int', 2864),
              ('state_increment', 'int', 2870),
              ('mode_a', 'int', 2938),
              ('frames_a', 'int', 2940),
              ('mode_b', 'int', 2944),
              ('mode_c', 'int', 2950),
              ('frames_b', 'int', 2952),
              ('mode_d', 'int', 2956),
              ('flag_required', 'int', 2967),
              ('step_small', 'float', 2975),
              ('step_large', 'float', 2995),
              ('hold_frames', 'int', 3015),
              ('distance_cap', 'float', 3022)]},
 {'name': 'stage_audio',
  'kind': 'int_dict',
  'pcs': {'lobby_flag': 3057,
          'first_stage_desc_param': 3078,
          'second_stage_desc_param': 3083,
          'third_stage_desc_param': 3102,
          'continue_stage_desc_param': 3114}}]


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
		index_to_value[index] = str(value)
	for index, value in index_to_value.items():
		string_entries[index] = value


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


def set_nested(container, dotted_name, value):
	parts = dotted_name.split(".")
	cursor = container
	for part in parts[:-1]:
		cursor = cursor.setdefault(part, {})
	cursor[parts[-1]] = value


def get_nested(container, dotted_name):
	cursor = container
	for part in dotted_name.split("."):
		cursor = cursor[part]
	return cursor


def export_spec(spec, pc_to_instruction, pc_to_string_index, string_entries):
	kind = spec["kind"]
	if kind == "string_list":
		return read_string_list(string_entries, pc_to_string_index, spec["pcs"])
	if kind == "string":
		return read_push_str(string_entries, pc_to_string_index, spec["pc"])[0]
	if kind == "int":
		return read_push_int(pc_to_instruction, spec["pc"])
	if kind == "float":
		return read_push_float(pc_to_instruction, spec["pc"])
	if kind == "int_list":
		return read_int_list(pc_to_instruction, spec["pcs"])
	if kind == "float_list":
		return read_float_list(pc_to_instruction, spec["pcs"])
	if kind == "int_dict":
		return {key: read_push_int(pc_to_instruction, pc) for key, pc in spec["pcs"].items()}
	if kind == "string_dict":
		return {key: read_push_str(string_entries, pc_to_string_index, pc)[0] for key, pc in spec["pcs"].items()}
	if kind == "int_matrix":
		return [read_int_list(pc_to_instruction, group) for group in spec["pcs_groups"]]
	if kind == "mixed_dict":
		result = {}
		for key, value_type, pc in spec["entries"]:
			if value_type == "int":
				result[key] = read_push_int(pc_to_instruction, pc)
			elif value_type == "float":
				result[key] = read_push_float(pc_to_instruction, pc)
			elif value_type == "string":
				result[key] = read_push_str(string_entries, pc_to_string_index, pc)[0]
			else:
				raise ValueError(f"Unsupported value_type {value_type!r} in spec {spec['name']}")
		return result
	raise ValueError(f"Unsupported spec kind: {kind}")


def apply_spec(spec, semantic, pc_to_instruction, pc_to_string_index, string_entries):
	value = get_nested(semantic, spec["name"])
	kind = spec["kind"]
	if kind == "string_list":
		write_push_str_group(string_entries, pc_to_string_index, spec["pcs"], value)
		return
	if kind == "string":
		write_push_str_group(string_entries, pc_to_string_index, [spec["pc"]], [value])
		return
	if kind == "int":
		write_push_int(pc_to_instruction, spec["pc"], value)
		return
	if kind == "float":
		write_push_float(pc_to_instruction, spec["pc"], value)
		return
	if kind == "int_list":
		write_int_list(pc_to_instruction, spec["pcs"], value, spec["name"])
		return
	if kind == "float_list":
		write_float_list(pc_to_instruction, spec["pcs"], value, spec["name"])
		return
	if kind == "int_dict":
		for key, pc in spec["pcs"].items():
			write_push_int(pc_to_instruction, pc, value[key])
		return
	if kind == "string_dict":
		for key, pc in spec["pcs"].items():
			write_push_str_group(string_entries, pc_to_string_index, [pc], [value[key]])
		return
	if kind == "int_matrix":
		if len(value) != len(spec["pcs_groups"]):
			raise ValueError(f"{spec['name']} must contain {len(spec['pcs_groups'])} rows, got {len(value)}")
		for row_index, (pcs, row_values) in enumerate(zip(spec["pcs_groups"], value)):
			write_int_list(pc_to_instruction, pcs, row_values, f"{spec['name']}[{row_index}]")
		return
	if kind == "mixed_dict":
		for key, value_type, pc in spec["entries"]:
			item = value[key]
			if value_type == "int":
				write_push_int(pc_to_instruction, pc, item)
			elif value_type == "float":
				write_push_float(pc_to_instruction, pc, item)
			elif value_type == "string":
				write_push_str_group(string_entries, pc_to_string_index, [pc], [item])
			else:
				raise ValueError(f"Unsupported value_type {value_type!r} in spec {spec['name']}")
		return
	raise ValueError(f"Unsupported spec kind: {kind}")


def load_document(pkc_path):
	return generic.decode_pkc(pkc_path)


def save_document(path, document):
	raw = generic.encode_pkc_document(document)
	with open(path, "wb") as outfile:
		outfile.write(raw)


def export_semantic(document):
	pc_to_instruction, pc_to_string_index = build_pc_maps(document)
	string_entries = document["string_section"]["entries"]
	semantic = {
		"format": FORMAT_VERSION,
		"source_basename": document.get("source_basename"),
	}
	for spec in SPECS:
		set_nested(semantic, spec["name"], export_spec(spec, pc_to_instruction, pc_to_string_index, string_entries))
	return semantic


def apply_semantic(document, semantic):
	if semantic.get("format") != FORMAT_VERSION:
		raise ValueError(f"Unsupported JSON format: {semantic.get('format')!r}")
	pc_to_instruction, pc_to_string_index = build_pc_maps(document)
	string_entries = document["string_section"]["entries"]
	for spec in SPECS:
		apply_spec(spec, semantic, pc_to_instruction, pc_to_string_index, string_entries)


def command_export(args):
	document = load_document(args.pkc_path)
	semantic = export_semantic(document)
	output_path = args.output or default_json_output(args.pkc_path)
	dump_json(output_path, semantic)
	print(f"Exported {output_path}")


def command_import(args):
	document = load_document(args.original_pkc)
	semantic = load_json(args.json_path)
	apply_semantic(document, semantic)
	output_path = args.output_pkc or default_pkc_output(args.original_pkc)
	save_document(output_path, document)
	print(f"Wrote {output_path}")


def command_verify(args):
	document = load_document(args.pkc_path)
	semantic = export_semantic(document)
	rebuilt_document = load_document(args.pkc_path)
	apply_semantic(rebuilt_document, semantic)
	with open(args.pkc_path, "rb") as infile:
		original_raw = infile.read()
	rebuilt_raw = generic.encode_pkc_document(rebuilt_document)
	matches = rebuilt_raw == original_raw
	print(f"Match: {'YES' if matches else 'NO'}")
	if not matches:
		for index, (left, right) in enumerate(zip(rebuilt_raw, original_raw)):
			if left != right:
				print(f"First mismatch at 0x{index:X}: rebuilt=0x{left:02X}, original=0x{right:02X}")
				break
		if len(rebuilt_raw) != len(original_raw):
			print(f"Length mismatch: rebuilt={len(rebuilt_raw)}, original={len(original_raw)}")
		raise SystemExit(1)


def build_arg_parser():
	parser = argparse.ArgumentParser(description=TOOL_DESCRIPTION)
	subparsers = parser.add_subparsers(dest="command", required=True)
	p = subparsers.add_parser("export")
	p.add_argument("pkc_path")
	p.add_argument("output", nargs="?")
	p.set_defaults(func=command_export)
	p = subparsers.add_parser("import")
	p.add_argument("json_path")
	p.add_argument("original_pkc")
	p.add_argument("output_pkc", nargs="?")
	p.set_defaults(func=command_import)
	p = subparsers.add_parser("verify")
	p.add_argument("pkc_path")
	p.set_defaults(func=command_verify)
	return parser


def main():
	parser = build_arg_parser()
	args = parser.parse_args()
	args.func(args)


if __name__ == "__main__":
	main()
