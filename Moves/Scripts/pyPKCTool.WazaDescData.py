#!/usr/bin/env python3
import argparse
import json
import os
import re
import struct
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
	sys.path.insert(0, SCRIPT_DIR)

try:
	import pyPKCToolBase as generic
	from pyPKCEnumLib import MoveEnum, TypeEnum
except ImportError as exc:
	print(f"Failed to import support modules: {exc}", file=sys.stderr)
	sys.exit(1)

FORMAT_VERSION = 'wazadesc-edit-json-1'
VARARGS_SENTINEL = 0xFFFFFFFF
MOVE_ID_TO_SYMBOL = {int(member): member.name for member in MoveEnum}
MOVE_SYMBOL_TO_ID = {member.name: int(member) for member in MoveEnum}
TYPE_ID_TO_SYMBOL = {int(member): member.name for member in TypeEnum}
TYPE_SYMBOL_TO_ID = {member.name: int(member) for member in TypeEnum}
MOVE_ENUM_RE = re.compile(r'^MOVE_(\d{1,4})$')
PUSH_INT = generic.PKCOpCode.push_int.value
PUSH_FLOAT = generic.PKCOpCode.push_s_f.value
PUSH_DAT = generic.PKCOpCode.push_dat.value
CALL_EXT_0 = generic.PKCOpCode.call_ext_0.value
CALL_EXT_1 = generic.PKCOpCode.call_ext_1.value
POP = generic.PKCOpCode.pop.value
NOP_0 = generic.PKCOpCode.nop_0.value
FUNCTION_PROLOGUE = generic.PKCOpCode.function_prologue.value
FUNCTION_EPILOGUE = generic.PKCOpCode.function_epilogue.value


PERCENTAGE_FIELD_NAMES = {
	'attack_angle',
	'critical_rate',
	'knock_power',
	'lunge_speed',
	'pre_attack_angle',
	'range_horizontal_width',
	'range_length',
	'range_speed',
	'range_vertical_width',
	'special_stat_rate_self',
	'special_stat_rate',
}


def format_percentage_value(value):
	if isinstance(value, bool):
		return value
	if isinstance(value, int):
		return f"{value * 100}%"
	if isinstance(value, float):
		text = f"{value * 100:.10f}".rstrip('0').rstrip('.')
		if text == '-0':
			text = '0'
		return f"{text}%"
	return value


def parse_percentage_value(value):
	if isinstance(value, (int, float)):
		return float(value) / 100.0
	if isinstance(value, str):
		text = value.strip()
		if text.endswith('%'):
			text = text[:-1].strip()
		return float(text) / 100.0
	raise ValueError(f'Invalid percentage value: {value!r}')

FIELD_NAME_MAP = {
	'WDC_NAME': 'name_id',
	'WDC_EID': 'effect_id',
	'WDC_TYPE': 'type',
	'WDC_F_ATTACKING': 'attacking_flags',
	'WDC_RANGE_TYPE': 'range_type',
	'WDC_RANGE_LENGTH': 'range_length',
	'WDC_RANGE_V_WIDTH': 'range_vertical_width',
	'WDC_RANGE_H_WIDTH': 'range_horizontal_width',
	'WDC_RANGE_SPEED': 'range_speed',
	'WDC_STAR': 'star',
	'WDC_DESC': 'description_id',
	'WDC_ATP': 'attack_power',
	'WDC_ATP_ENEMY': 'attack_power_enemy',
	'WDC_ATP_SP': 'special_attack_power',
	'WDC_AUTO_AIM': 'auto_aim',
	'WDC_BEAT_NUM': 'beat_count',
	'WDC_BLOW_NUM': 'blow_count',
	'WDC_CRITICAL_RATE': 'critical_rate',
	'WDC_DEG_ATTACKING': 'attack_angle',
	'WDC_DEG_BEFORE_ATTACKING': 'pre_attack_angle',
	'WDC_FRIENDLY_FIRE': 'friendly_fire',
	'WDC_F_AFTER': 'after_flags',
	'WDC_F_AFTER_ENEMY': 'after_flags_enemy',
	'WDC_F_ATTACKING_POSE': 'attacking_pose_flags',
	'WDC_F_BEFORE': 'before_flags',
	'WDC_F_BEFORE_ENEMY': 'before_flags_enemy',
	'WDC_F_NEXT': 'next_flags',
	'WDC_KNOCK_POWER': 'knock_power',
	'WDC_KNOCK_TIME': 'knock_time',
	'WDC_LUNGE_EXECUTING': 'lunge_executing',
	'WDC_LUNGE_SPEED': 'lunge_speed',
	'WDC_MOTION': 'motion_id',
	'WDC_NEXT_WID': 'next_move',
	'WDC_NONE_HIT': 'allow_no_hit',
	'WDC_SE_HIT': 'se_hit',
	'WDC_SE_SHOOT': 'se_shoot',
	'WDC_SHOOT_NUM': 'shoot_count',
	'WDC_SP_STAT_OBJ': 'special_stat_object',
	'WDC_SP_STAT_RATE': 'special_stat_rate',
	'WDC_SP_STAT_RATE_SELF': 'special_stat_rate_self',
	'WDC_SP_STAT_TYPE': 'special_stat_type',
	'WDC_SP_STAT_TYPE_SELF': 'special_stat_type_self',
	'WDC_STAMINA': 'stamina',
}


def int_to_u24_signed(value):
	value = int(value)
	if not -0x800000 <= value <= 0x7FFFFF:
		raise ValueError(f'Signed 24-bit value out of range: {value}')
	return value & 0xFFFFFF


def move_symbol(value):
	return MOVE_ID_TO_SYMBOL.get(int(value), f'MOVE_{int(value):04d}')


def move_value_to_id(value):
	if isinstance(value, int):
		return value
	if isinstance(value, str):
		if value in MOVE_SYMBOL_TO_ID:
			return MOVE_SYMBOL_TO_ID[value]
		match = MOVE_ENUM_RE.fullmatch(value)
		if match is not None:
			return int(match.group(1))
		return int(value)
	raise ValueError(f'Invalid move value: {value!r}')


def type_symbol(value):
	return TYPE_ID_TO_SYMBOL.get(int(value), f'TYPE_{int(value)}')


def type_value_to_id(value):
	if isinstance(value, int):
		return value
	if isinstance(value, str):
		if value in TYPE_SYMBOL_TO_ID:
			return TYPE_SYMBOL_TO_ID[value]
		return int(value)
	raise ValueError(f'Invalid type value: {value!r}')


class StackValue:
	def __init__(self, kind, value, source_instruction_index):
		self.kind = kind
		self.value = value
		self.source_instruction_index = source_instruction_index


class SimpleStack:
	def __init__(self):
		self.items = []

	def push(self, item):
		self.items.append(item)

	def popn(self, count):
		if count <= 0:
			return []
		if len(self.items) < count:
			missing = count - len(self.items)
			result = [StackValue('unknown', '<?>', None) for _ in range(missing)] + self.items[:]
			self.items.clear()
			return result
		result = self.items[-count:]
		del self.items[-count:]
		return result


class RowTemplate:
	def __init__(self, move_id, field_values, field_kinds, start_instruction_index, call_instruction_index, nop_instruction_index, pop_instruction_index, instructions):
		self.move_id = move_id
		self.field_values = field_values
		self.field_kinds = field_kinds
		self.start_instruction_index = start_instruction_index
		self.call_instruction_index = call_instruction_index
		self.nop_instruction_index = nop_instruction_index
		self.pop_instruction_index = pop_instruction_index
		self.start_byte_offset = instructions[start_instruction_index]['byte_offset']
		end_instruction = instructions[pop_instruction_index]
		self.end_byte_offset = end_instruction['byte_offset'] + end_instruction['length_bytes']
		self.start_pc_units = instructions[start_instruction_index]['pc_units']
		self.end_pc_units = instructions[pop_instruction_index]['pc_units']


def evaluate_binary(opname, left, right):
	left_value = left.value
	right_value = right.value
	result_kind = 'float' if 'float' in (left.kind, right.kind) else 'int'
	if opname == 'add':
		return result_kind, left_value + right_value
	if opname == 'sub':
		return result_kind, left_value - right_value
	if opname == 'multiply':
		return result_kind, left_value * right_value
	if opname == 'divide':
		return 'float', left_value / right_value
	if opname == 'cmp_eq':
		return 'int', 1 if left_value == right_value else 0
	if opname == 'cmp_neq':
		return 'int', 1 if left_value != right_value else 0
	if opname == 'cmp_lt':
		return 'int', 1 if left_value < right_value else 0
	if opname == 'cmp_geq':
		return 'int', 1 if left_value >= right_value else 0
	if opname == 'cmp_leq':
		return 'int', 1 if left_value <= right_value else 0
	if opname == 'cmp_gt':
		return 'int', 1 if left_value > right_value else 0
	if opname == 'bitwise_and':
		return 'int', int(left_value) & int(right_value)
	if opname == 'bitwise_xor':
		return 'int', int(left_value) ^ int(right_value)
	if opname == 'bitwise_or':
		return 'int', int(left_value) | int(right_value)
	if opname == 'bitwise_rshift':
		return 'int', int(left_value) >> int(right_value)
	return 'int', int(left_value) << int(right_value)


def convert_field_value_for_export(wdc_name, value):
	field_name = FIELD_NAME_MAP.get(wdc_name, wdc_name.lower())
	if field_name in PERCENTAGE_FIELD_NAMES:
		return format_percentage_value(value)
	if wdc_name == 'WDC_TYPE' and isinstance(value, int):
		return type_symbol(value)
	if wdc_name == 'WDC_NEXT_WID' and isinstance(value, int) and value > 0:
		return move_symbol(value)
	return value


def convert_field_value_for_import(wdc_name, value):
	field_name = FIELD_NAME_MAP.get(wdc_name, wdc_name.lower())
	if field_name in PERCENTAGE_FIELD_NAMES:
		return parse_percentage_value(value)
	if wdc_name == 'WDC_TYPE':
		return type_value_to_id(value)
	if wdc_name == 'WDC_NEXT_WID' and isinstance(value, str) and (value in MOVE_SYMBOL_TO_ID or MOVE_ENUM_RE.fullmatch(value)):
		return move_value_to_id(value)
	return value


def parse_row_templates(instructions):
	stack = SimpleStack()
	call_records = []
	binary_ops = {
		'add', 'sub', 'multiply', 'divide', 'cmp_eq', 'cmp_neq', 'cmp_lt', 'cmp_geq', 'cmp_leq', 'cmp_gt',
		'bitwise_and', 'bitwise_xor', 'bitwise_or', 'bitwise_rshift', 'bitwise_lshift'
	}
	for instruction in instructions:
		opname = instruction['opname']
		decoded = instruction.get('decoded', {})
		instruction_index = instruction['index']
		opcode = instruction.get('opcode')
		if opcode == PUSH_INT:
			stack.push(StackValue('int', decoded['value_signed'], instruction_index))
		elif opcode == PUSH_FLOAT:
			stack.push(StackValue('float', decoded['value'], instruction_index))
		elif opcode == PUSH_DAT:
			stack.push(StackValue('data', decoded.get('value'), instruction_index))
		elif opname in binary_ops:
			right = stack.popn(1)[0]
			left = stack.popn(1)[0]
			if left.kind in ('int', 'float') and right.kind in ('int', 'float'):
				result_kind, result_value = evaluate_binary(opname, left, right)
				stack.push(StackValue(result_kind, result_value, instruction_index))
			else:
				stack.push(StackValue('expr', opname, instruction_index))
		elif opname in ('negate', 'complement', 'is_zero'):
			item = stack.popn(1)[0]
			if item.kind in ('int', 'float'):
				if opname == 'negate':
					stack.push(StackValue(item.kind, -item.value, instruction_index))
				elif opname == 'complement':
					stack.push(StackValue('int', ~int(item.value), instruction_index))
				else:
					stack.push(StackValue('int', 1 if item.value == 0 else 0, instruction_index))
			else:
				stack.push(StackValue('expr', opname, instruction_index))
		elif opcode in (CALL_EXT_0, CALL_EXT_1):
			function_name = decoded.get('function_name')
			argc = decoded.get('resolved_argc_from_next_nop_0') if opcode == CALL_EXT_1 else None
			if argc is None:
				declared = decoded.get('declared_argc')
				argc = 0 if declared == VARARGS_SENTINEL else declared
			arg_values = stack.popn(argc)
			call_records.append({
				'instruction_index': instruction_index,
				'pc_units': instruction['pc_units'],
				'function_name': function_name,
				'args': arg_values,
				'argc': argc,
			})
			stack.push(StackValue('call', function_name, instruction_index))
		elif opname in ('store', 'store_ptr', 'pop'):
			stack.popn(1)
		elif opname == 'call_imm':
			stack.popn(1)
			stack.push(StackValue('expr', 'call_imm', instruction_index))
		elif opcode in (FUNCTION_PROLOGUE, FUNCTION_EPILOGUE):
			stack = SimpleStack()

	init_call = None
	row_calls = []
	for record in call_records:
		if record['function_name'] == 'InitWazaDescs':
			init_call = record
		elif record['function_name'] == 'SetWazaDescRow':
			row_calls.append(record)
	if init_call is None:
		raise ValueError('InitWazaDescs call was not found')
	if len(row_calls) < 3:
		raise ValueError('Expected row0, header row and data rows')
	row0 = row_calls[0]
	header_row = row_calls[1]
	if len(header_row['args']) < 2 or header_row['args'][0].kind != 'int' or header_row['args'][0].value != -1:
		raise ValueError('Header row did not match expected structure')
	header_wdc_names = []
	for item in header_row['args'][1:]:
		if item.kind != 'data' or not isinstance(item.value, str):
			raise ValueError('Header row contains non-data entries')
		header_wdc_names.append(item.value)
	rows = []
	for record in row_calls[2:]:
		args = record['args']
		if len(args) != len(header_wdc_names) + 1:
			raise ValueError(f"Unexpected SetWazaDescRow argc={len(args)} at PC 0x{record['pc_units']:04X}")
		move_arg = args[0]
		if move_arg.kind != 'int':
			raise ValueError(f"Move ID was not a literal int at PC 0x{record['pc_units']:04X}")
		field_values = []
		field_kinds = []
		for item in args[1:]:
			if item.kind not in ('int', 'float'):
				raise ValueError(f"Unsupported field kind {item.kind!r} at PC 0x{record['pc_units']:04X}")
			field_values.append(item.value)
			field_kinds.append(item.kind)
		call_instruction_index = record['instruction_index']
		nop_instruction_index = call_instruction_index + 1
		pop_instruction_index = nop_instruction_index + 1
		if instructions[nop_instruction_index]['opcode'] != NOP_0 or instructions[pop_instruction_index]['opcode'] != POP:
			raise ValueError(f"Malformed SetWazaDescRow tail near PC 0x{record['pc_units']:04X}")
		rows.append(RowTemplate(
			move_id=move_arg.value,
			field_values=field_values,
			field_kinds=field_kinds,
			start_instruction_index=move_arg.source_instruction_index,
			call_instruction_index=call_instruction_index,
			nop_instruction_index=nop_instruction_index,
			pop_instruction_index=pop_instruction_index,
			instructions=instructions,
		))
	return init_call, row0, header_row, header_wdc_names, rows


def read_and_parse(path):
	pkc = generic.read_pkc(path)
	string_entries = generic.decode_ascii_cstring_pool(pkc['string_raw'], pkc['string_count'])
	if string_entries is None:
		string_entries = []
	data_section = generic.decode_data_section(pkc['data_raw'], pkc['data_count'])
	instructions = generic.decode_instruction_stream(pkc['code_raw'] + pkc['trailing_raw'], string_entries, data_section)
	init_call, row0, header_row, header_wdc_names, rows = parse_row_templates(instructions)
	return pkc, instructions, init_call, row0, header_row, header_wdc_names, rows


def export_document(pkc_path):
	pkc, instructions, init_call, row0, header_row, header_wdc_names, rows = read_and_parse(pkc_path)
	field_order = [FIELD_NAME_MAP.get(name, name.lower()) for name in header_wdc_names]
	entries = []
	for row in rows:
		fields = {}
		for wdc_name, value in zip(header_wdc_names, row.field_values):
			field_name = FIELD_NAME_MAP.get(wdc_name, wdc_name.lower())
			fields[field_name] = convert_field_value_for_export(wdc_name, value)
		entries.append({
			'block_index': row.move_id,
			'move': move_symbol(row.move_id),
			'pc_range': {
				'start': f"0x{row.start_pc_units:04X}",
				'end': f"0x{row.end_pc_units:04X}",
			},
			'fields': fields,
		})
	return {
		'format': FORMAT_VERSION,
		'source_file': pkc['basename'],
		'notes': {
			'edit_authoritative_data_under': 'entries[].fields',
			'move_uses_enum_symbols': True,
			'type_uses_enum_symbols': True,
			'adding_or_removing_move_rows': False,
		},
		'field_order': field_order,
		'entries': entries,
	}


def encode_push_int(value):
	return bytes([PUSH_INT]) + int_to_u24_signed(value).to_bytes(3, 'big')


def encode_push_float(value):
	return bytes([PUSH_FLOAT, 0x00, 0x00, 0x00]) + struct.pack('>f', float(value))


def function_index_by_name(function_name):
	for index, pair in enumerate(generic.script_functions):
		name, _argc = pair
		if name == function_name:
			return index
	raise KeyError(function_name)


SET_WAZADESCROW_INDEX = function_index_by_name('SetWazaDescRow')


def encode_call_ext1(function_index, argc):
	return bytes([CALL_EXT_1]) + int(function_index).to_bytes(3, 'big') + bytes([NOP_0]) + int(argc).to_bytes(3, 'big')


def encode_pop():
	return bytes([POP, 0x00, 0x00, 0x00])


def normalize_entry(raw_entry):
	if not isinstance(raw_entry, dict):
		raise ValueError('Each entry must be an object')
	fields = raw_entry.get('fields')
	if not isinstance(fields, dict):
		raise ValueError(f"Entry {raw_entry.get('move')} must contain a fields object")
	return {
		'move_id': move_value_to_id(raw_entry.get('move')),
		'fields': fields,
	}


def build_row_bytes(move_id, fields_by_name, header_wdc_names, field_kinds):
	out = bytearray()
	out.extend(encode_push_int(move_id))
	for wdc_name, kind in zip(header_wdc_names, field_kinds):
		field_name = FIELD_NAME_MAP.get(wdc_name, wdc_name.lower())
		if field_name not in fields_by_name:
			raise ValueError(f"Missing field {field_name!r} for move {move_id}")
		value = convert_field_value_for_import(wdc_name, fields_by_name[field_name])
		if kind == 'int':
			out.extend(encode_push_int(value))
		else:
			out.extend(encode_push_float(value))
	out.extend(encode_call_ext1(SET_WAZADESCROW_INDEX, len(header_wdc_names) + 1))
	out.extend(encode_pop())
	return bytes(out)


def build_rebuilt_code(original_pkc, header_wdc_names, rows, document):
	entry_map = {}
	for raw_entry in document.get('entries', []):
		entry = normalize_entry(raw_entry)
		entry_map[entry['move_id']] = entry
	original_fields_by_move = {}
	for row in rows:
		fields = {}
		for wdc_name, value in zip(header_wdc_names, row.field_values):
			field_name = FIELD_NAME_MAP.get(wdc_name, wdc_name.lower())
			fields[field_name] = convert_field_value_for_export(wdc_name, value)
		original_fields_by_move[row.move_id] = fields
		if row.move_id not in entry_map:
			entry_map[row.move_id] = {'move_id': row.move_id, 'fields': fields}
	first_row = rows[0]
	last_row = rows[-1]
	prefix = original_pkc['code_raw'][:first_row.start_byte_offset]
	suffix = original_pkc['code_raw'][last_row.end_byte_offset:]
	parts = [prefix]
	for row in rows:
		entry = entry_map[row.move_id]
		if entry['fields'] == original_fields_by_move[row.move_id]:
			parts.append(original_pkc['code_raw'][row.start_byte_offset:row.end_byte_offset])
		else:
			parts.append(build_row_bytes(row.move_id, entry['fields'], header_wdc_names, row.field_kinds))
	parts.append(suffix)
	return b''.join(parts)


def write_pkc(path, original_pkc, rebuilt_code):
	header = bytes.fromhex(original_pkc['magic_hex']) + struct.pack('>7I',
		original_pkc['flags'],
		len(rebuilt_code),
		original_pkc['data_size'],
		original_pkc['data_count'],
		original_pkc['string_size'],
		original_pkc['string_count'],
		original_pkc['reserved'],
	)
	payload = header + rebuilt_code + original_pkc['data_raw'] + original_pkc['string_raw'] + original_pkc['trailing_raw']
	with open(path, 'wb') as outfile:
		outfile.write(payload)


def derive_json_output_path(pkc_path):
	base, _ext = os.path.splitext(pkc_path)
	return base + '.json'


def derive_rebuilt_output_path(original_pkc_path):
	base, ext = os.path.splitext(original_pkc_path)
	if not ext:
		ext = '.pkc'
	return f'{base}.rebuilt{ext}'


def load_json_document(json_path):
	with open(json_path, 'r', encoding='utf-8') as infile:
		document = json.load(infile)
	if document.get('format') != FORMAT_VERSION:
		raise ValueError(f"Unsupported JSON format: expected {FORMAT_VERSION!r}, got {document.get('format')!r}")
	if not isinstance(document.get('entries'), list):
		raise ValueError('JSON document must contain an entries list')
	return document


def command_summary(args):
	pkc, instructions, init_call, row0, header_row, header_wdc_names, rows = read_and_parse(args.pkc_path)
	print(f"File: {os.path.basename(args.pkc_path)}")
	print(f"Move rows: {len(rows)}")
	print(f"Columns: {len(header_wdc_names)}")
	for name in header_wdc_names:
		print(f"\t{FIELD_NAME_MAP.get(name, name.lower())} ({name})")


def command_export(args):
	document = export_document(args.pkc_path)
	output_path = args.output if args.output else derive_json_output_path(args.pkc_path)
	with open(output_path, 'w', encoding='utf-8') as outfile:
		json.dump(document, outfile, ensure_ascii=False, indent='\t')
		outfile.write('\n')
	print(f"Exported {len(document['entries'])} move rows to {output_path}")


def command_import(args):
	original_pkc, instructions, init_call, row0, header_row, header_wdc_names, rows = read_and_parse(args.original_pkc)
	document = load_json_document(args.json_path)
	rebuilt_code = build_rebuilt_code(original_pkc, header_wdc_names, rows, document)
	output_pkc = args.output_pkc if args.output_pkc else derive_rebuilt_output_path(args.original_pkc)
	write_pkc(output_pkc, original_pkc, rebuilt_code)
	print(f"Imported {len(document['entries'])} move rows into {output_pkc}")


def command_verify(args):
	original_pkc, instructions, init_call, row0, header_row, header_wdc_names, rows = read_and_parse(args.pkc_path)
	document = export_document(args.pkc_path)
	rebuilt_code = build_rebuilt_code(original_pkc, header_wdc_names, rows, document)
	header = bytes.fromhex(original_pkc['magic_hex']) + struct.pack('>7I',
		original_pkc['flags'],
		len(rebuilt_code),
		original_pkc['data_size'],
		original_pkc['data_count'],
		original_pkc['string_size'],
		original_pkc['string_count'],
		original_pkc['reserved'],
	)
	rebuilt = header + rebuilt_code + original_pkc['data_raw'] + original_pkc['string_raw'] + original_pkc['trailing_raw']
	with open(args.pkc_path, 'rb') as infile:
		original = infile.read()
	print(f"Match: {'YES' if rebuilt == original else 'NO'}")
	print(f"Move rows: {len(rows)}")


def build_arg_parser():
	parser = argparse.ArgumentParser(description='Edit WazaDescData.pkc as move-centric JSON.')
	subparsers = parser.add_subparsers(dest='command', required=True)
	parser_summary = subparsers.add_parser('summary', help='Show a quick structural summary.')
	parser_summary.add_argument('pkc_path')
	parser_export = subparsers.add_parser('export', help='Export a PKC to editable JSON.')
	parser_export.add_argument('pkc_path')
	parser_export.add_argument('output', nargs='?')
	parser_import = subparsers.add_parser('import', help='Rebuild a PKC from exported JSON.')
	parser_import.add_argument('json_path')
	parser_import.add_argument('original_pkc')
	parser_import.add_argument('output_pkc', nargs='?')
	parser_verify = subparsers.add_parser('verify', help='Decode and immediately rebuild a PKC, then compare bytes.')
	parser_verify.add_argument('pkc_path')
	return parser


def main():
	parser = build_arg_parser()
	args = parser.parse_args()
	if args.command == 'summary':
		command_summary(args)
	elif args.command == 'export':
		command_export(args)
	elif args.command == 'import':
		command_import(args)
	elif args.command == 'verify':
		command_verify(args)
	else:
		parser.error(f'Unknown command: {args.command}')


if __name__ == '__main__':
	main()
