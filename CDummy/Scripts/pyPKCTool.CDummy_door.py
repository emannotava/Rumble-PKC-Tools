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

FORMAT_VERSION = 'cdummy-door-semantic-json-1'
TOOL_DESCRIPTION = 'Edit CDummy_door.pkc via semantic JSON.'
SPECS = [{'name': 'embedded_event_scripts',
  'kind': 'string_list',
  'pcs': [2786, 2792, 2798, 2804, 2810, 2816, 2822, 2828, 2834, 2840, 2846, 2852, 2858, 2864, 2870, 2876]},
 {'name': 'battle_royal_begin_item',
  'kind': 'mixed_dict',
  'entries': [('resume_state', 'int', 2893),
			  ('newitem_category', 'int', 2897),
			  ('newitem_subcategory', 'int', 2902),
			  ('event_script', 'string', 2904),
			  ('spawn_wait_state', 'int', 2915),
			  ('spawn_owner_slot', 'int', 2917),
			  ('spawn_alive_required', 'int', 2922),
			  ('position_blend_x', 'float', 2965),
			  ('position_blend_y', 'float', 2982),
			  ('position_blend_z', 'float', 2986),
			  ('position_scale_a', 'float', 2992),
			  ('position_scale_b', 'float', 2996),
			  ('required_flag', 'int', 3020),
			  ('pad_button_mask', 'int', 3044),
			  ('show_notice_type', 'int', 3047),
			  ('yield_state', 'int', 3050)]},
 {'name': 'door_notice_strings', 'kind': 'int_list', 'pcs': [3256, 3267]},
 {'name': 'reward_scaling',
  'kind': 'mixed_dict',
  'entries': [('grade_bias', 'int', 3454),
			  ('rand_grade_min', 'int', 3457),
			  ('rand_grade_max', 'int', 3458),
			  ('rand_bonus_min', 'int', 3462),
			  ('rand_bonus_max', 'int', 3463),
			  ('frame_divisor_exponent', 'int', 3480),
			  ('frame_divisor_float', 'float', 3483),
			  ('frame_divisor_int', 'int', 3493),
			  ('time_divisor_exponent', 'int', 3497),
			  ('time_divisor_float', 'float', 3500),
			  ('time_divisor_int', 'int', 3510),
			  ('unit_divisor_exponent', 'int', 3514),
			  ('unit_divisor_float', 'float', 3517),
			  ('unit_divisor_int', 'int', 3527)]},
 {'name': 'money_item_spawn',
  'kind': 'mixed_dict',
  'entries': [('angle_weight_a', 'float', 3708),
			  ('angle_weight_b', 'float', 3711),
			  ('pow_angle_base', 'int', 3714),
			  ('pow_angle_exponent', 'int', 3717),
			  ('speed_min', 'float', 3727),
			  ('speed_max', 'float', 3729),
			  ('offset_min', 'float', 3733),
			  ('offset_max', 'float', 3735),
			  ('dir_min', 'int', 3739),
			  ('dir_max', 'int', 3740),
			  ('register_slot_a', 'int', 3745),
			  ('register_slot_b', 'int', 3749),
			  ('register_slot_c', 'int', 3753),
			  ('item_script', 'string', 3757),
			  ('spawn_height', 'float', 3768)]},
 {'name': 'reward_probability_rows',
  'kind': 'int_matrix',
  'pcs_groups': [[3796, 3798, 3800],
				 [3803, 3805, 3807],
				 [3810, 3812, 3814],
				 [3817, 3819, 3821],
				 [3829, 3831, 3833],
				 [3836, 3838, 3840],
				 [3843, 3845, 3847],
				 [3850, 3852, 3854],
				 [3860, 3862, 3864]]},
 {'name': 'reward_money_values.primary',
  'kind': 'int_list',
  'pcs': [3917, 3919, 3921, 3923, 3929, 3931, 3933, 3935, 3940]},
 {'name': 'reward_money_values.secondary',
  'kind': 'int_list',
  'pcs': [3964, 3966, 3968, 3970, 3976, 3978, 3980, 3982, 3987]},
 {'name': 'difficulty_rank_labels', 'kind': 'int_list', 'pcs': [4006, 4011, 4016, 4028, 4033, 4038, 4043, 4048]},
 {'name': 'waza_desc_fields', 'kind': 'int_list', 'pcs': [4084, 4088, 4092, 4096, 4100]},
 {'name': 'message_balloon',
  'kind': 'mixed_dict',
  'entries': [('normal_template_mid', 'int', 4184),
			  ('alt_template_mid', 'int', 4195),
			  ('register_scale', 'float', 4206),
			  ('adjust_y', 'float', 4212)]}]


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
	parts = dotted_name.split('.')
	cursor = container
	for part in parts[:-1]:
		cursor = cursor.setdefault(part, {})
	cursor[parts[-1]] = value


def get_nested(container, dotted_name):
	cursor = container
	for part in dotted_name.split('.'):
		cursor = cursor[part]
	return cursor


def export_spec(spec, pc_to_instruction, pc_to_string_index, string_entries):
	kind = spec['kind']
	if kind == 'string_list':
		return read_string_list(string_entries, pc_to_string_index, spec['pcs'])
	if kind == 'string':
		return read_push_str(string_entries, pc_to_string_index, spec['pc'])[0]
	if kind == 'int':
		return read_push_int(pc_to_instruction, spec['pc'])
	if kind == 'float':
		return read_push_float(pc_to_instruction, spec['pc'])
	if kind == 'int_list':
		return read_int_list(pc_to_instruction, spec['pcs'])
	if kind == 'float_list':
		return read_float_list(pc_to_instruction, spec['pcs'])
	if kind == 'int_dict':
		return {key: read_push_int(pc_to_instruction, pc) for key, pc in spec['pcs'].items()}
	if kind == 'string_dict':
		return {key: read_push_str(string_entries, pc_to_string_index, pc)[0] for key, pc in spec['pcs'].items()}
	if kind == 'int_matrix':
		return [read_int_list(pc_to_instruction, group) for group in spec['pcs_groups']]
	if kind == 'mixed_dict':
		result = {}
		for key, value_type, pc in spec['entries']:
			if value_type == 'int':
				result[key] = read_push_int(pc_to_instruction, pc)
			elif value_type == 'float':
				result[key] = read_push_float(pc_to_instruction, pc)
			elif value_type == 'string':
				result[key] = read_push_str(string_entries, pc_to_string_index, pc)[0]
			else:
				raise ValueError(f"Unsupported value_type {value_type!r} in spec {spec['name']}")
		return result
	raise ValueError(f"Unsupported spec kind: {kind}")


def apply_spec(spec, semantic, pc_to_instruction, pc_to_string_index, string_entries):
	value = get_nested(semantic, spec['name'])
	kind = spec['kind']
	if kind == 'string_list':
		write_push_str_group(string_entries, pc_to_string_index, spec['pcs'], value)
		return
	if kind == 'string':
		write_push_str_group(string_entries, pc_to_string_index, [spec['pc']], [value])
		return
	if kind == 'int':
		write_push_int(pc_to_instruction, spec['pc'], value)
		return
	if kind == 'float':
		write_push_float(pc_to_instruction, spec['pc'], value)
		return
	if kind == 'int_list':
		write_int_list(pc_to_instruction, spec['pcs'], value, spec['name'])
		return
	if kind == 'float_list':
		write_float_list(pc_to_instruction, spec['pcs'], value, spec['name'])
		return
	if kind == 'int_dict':
		for key, pc in spec['pcs'].items():
			write_push_int(pc_to_instruction, pc, value[key])
		return
	if kind == 'string_dict':
		for key, pc in spec['pcs'].items():
			write_push_str_group(string_entries, pc_to_string_index, [pc], [value[key]])
		return
	if kind == 'int_matrix':
		if len(value) != len(spec['pcs_groups']):
			raise ValueError(f"{spec['name']} must contain {len(spec['pcs_groups'])} rows, got {len(value)}")
		for row_index, (pcs, row_values) in enumerate(zip(spec['pcs_groups'], value)):
			write_int_list(pc_to_instruction, pcs, row_values, f"{spec['name']}[{row_index}]")
		return
	if kind == 'mixed_dict':
		for key, value_type, pc in spec['entries']:
			item = value[key]
			if value_type == 'int':
				write_push_int(pc_to_instruction, pc, item)
			elif value_type == 'float':
				write_push_float(pc_to_instruction, pc, item)
			elif value_type == 'string':
				write_push_str_group(string_entries, pc_to_string_index, [pc], [item])
			else:
				raise ValueError(f"Unsupported value_type {value_type!r} in spec {spec['name']}")
		return
	raise ValueError(f"Unsupported spec kind: {kind}")


def load_document(pkc_path):
	return generic.decode_pkc(pkc_path)


def save_document(path, document):
	raw = generic.encode_pkc_document(document)
	with open(path, 'wb') as outfile:
		outfile.write(raw)


def export_semantic(document):
	pc_to_instruction, pc_to_string_index = build_pc_maps(document)
	string_entries = document['string_section']['entries']
	semantic = {
		'format': FORMAT_VERSION,
		'source_basename': document.get('source_basename'),
	}
	for spec in SPECS:
		set_nested(semantic, spec['name'], export_spec(spec, pc_to_instruction, pc_to_string_index, string_entries))
	return semantic


def apply_semantic(document, semantic):
	if semantic.get('format') != FORMAT_VERSION:
		raise ValueError(f"Unsupported JSON format: {semantic.get('format')!r}")
	pc_to_instruction, pc_to_string_index = build_pc_maps(document)
	string_entries = document['string_section']['entries']
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
	with open(args.pkc_path, 'rb') as infile:
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
	subparsers = parser.add_subparsers(dest='command', required=True)
	p = subparsers.add_parser('export')
	p.add_argument('pkc_path')
	p.add_argument('output', nargs='?')
	p.set_defaults(func=command_export)
	p = subparsers.add_parser('import')
	p.add_argument('json_path')
	p.add_argument('original_pkc')
	p.add_argument('output_pkc', nargs='?')
	p.set_defaults(func=command_import)
	p = subparsers.add_parser('verify')
	p.add_argument('pkc_path')
	p.set_defaults(func=command_verify)
	return parser


def main():
	parser = build_arg_parser()
	args = parser.parse_args()
	args.func(args)


if __name__ == '__main__':
	main()
