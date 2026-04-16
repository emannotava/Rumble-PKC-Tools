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

FORMAT_VERSION = 'cdummy-idle-semantic-json-1'
TOOL_DESCRIPTION = 'Edit CDummy_idle.pkc via semantic JSON.'
SPECS = [{'name': 'lifecycle', 'kind': 'mixed_dict', 'entries': [('show_on_init', 'int', 3)]}]


def dump_json(path, document):
	with open(path, 'w', encoding='utf-8', newline='\n') as outfile:
		outfile.write(json.dumps(document, ensure_ascii=False, indent='\t'))
		outfile.write('\n')


def load_json(path):
	with open(path, 'r', encoding='utf-8') as infile:
		return json.load(infile)


def basename_without_ext(path):
	return os.path.splitext(os.path.basename(path))[0]


def default_json_output(input_path):
	return basename_without_ext(input_path) + '.json'


def default_pkc_output(original_pkc_path):
	base, ext = os.path.splitext(original_pkc_path)
	return base + '.rebuilt' + ext


def build_pc_maps(document):
	pc_to_instruction = {}
	pc_to_string_index = {}
	for instruction in document['code_section']['instructions']:
		pc = int(instruction['pc_units'])
		pc_to_instruction[pc] = instruction
		if instruction['opname'] == 'push_str':
			decoded = instruction.get('decoded', {})
			pc_to_string_index[pc] = int(decoded['index'])
	return pc_to_instruction, pc_to_string_index


def expect_instruction(pc_to_instruction, pc, opname):
	instruction = pc_to_instruction.get(pc)
	if instruction is None:
		raise ValueError(f'Expected instruction at PC 0x{pc:04X}, but none was found')
	if instruction['opname'] != opname:
		raise ValueError(f"Expected {opname} at PC 0x{pc:04X}, found {instruction['opname']}")
	return instruction


def signed24_to_u24(value):
	integer = int(value)
	if not -(1 << 23) <= integer < (1 << 23):
		raise ValueError(f'Value out of signed 24-bit range: {integer}')
	return integer & 0xFFFFFF


def read_push_int(pc_to_instruction, pc):
	instruction = expect_instruction(pc_to_instruction, pc, 'push_int')
	return int(instruction.get('decoded', {}).get('value_signed'))


def write_push_int(pc_to_instruction, pc, value):
	instruction = expect_instruction(pc_to_instruction, pc, 'push_int')
	instruction['imm'] = signed24_to_u24(value)
	instruction['imm_hex'] = f"0x{instruction['imm']:06X}"
	instruction['decoded'] = {'type': 'int24', 'value_signed': int(value), 'value_unsigned': instruction['imm']}


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
	result = {}
	for key, value_type, pc in spec['entries']:
		result[key] = read_push_int(pc_to_instruction, pc)
	return result


def apply_spec(spec, semantic, pc_to_instruction, pc_to_string_index, string_entries):
	value = get_nested(semantic, spec['name'])
	for key, value_type, pc in spec['entries']:
		write_push_int(pc_to_instruction, pc, value[key])


def load_document(pkc_path):
	return generic.decode_pkc(pkc_path)


def save_document(path, document):
	with open(path, 'wb') as outfile:
		outfile.write(generic.encode_pkc_document(document))


def export_semantic(document):
	pc_to_instruction, pc_to_string_index = build_pc_maps(document)
	string_entries = document['string_section']['entries']
	semantic = {'format': FORMAT_VERSION, 'source_basename': document.get('source_basename')}
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
	print(f'Exported {output_path}')


def command_import(args):
	document = load_document(args.original_pkc)
	semantic = load_json(args.json_path)
	apply_semantic(document, semantic)
	output_path = args.output_pkc or default_pkc_output(args.original_pkc)
	save_document(output_path, document)
	print(f'Wrote {output_path}')


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
