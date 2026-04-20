#!/usr/bin/env python3
import importlib.util
import copy
import json
import math
import os
import sys
import tkinter as tk
from tkinter import filedialog, messagebox, ttk


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ICON_DIR = os.path.join(SCRIPT_DIR, 'icon')
BACKEND_PATH = os.path.join(SCRIPT_DIR, 'pyPKCTool.PiiPropData.py')
ENUM_LIB_PATH = os.path.join(SCRIPT_DIR, 'pyPKCEnumLib.py')
FALLBACK_ICON_NAME = '_fallback.png'
SIDEBAR_ICON_SIZE = 48
MAIN_ICON_SIZE = 128
SIDEBAR_ROWHEIGHT = 52


FIELD_LAYOUT = [
	[("HP", "hp"), ("ATK", "attack"), ("DEF", "defense"), ("SPE", "speed")],
	[
		("Boss HP (%)", "boss_hp_multiplier"),
		("Boss ATK (%)", "boss_attack_multiplier"),
		("Boss DEF (%)", "boss_defense_multiplier"),
		("Boss SPE", "boss_speed_override"),
		("CBoss Role", "used_by_cboss_unknown_role"),
		("Boss Scalar X1/X3", "boss_scalar_x1_or_x3"),
	],
	[("Wild Move", "wild_move"), ("Unused 2nd Wild Move", "wild_move_2nd_unused")],
	[("Size Modifier (%)", "a_size_or_physical_size"), ("Walk Speed (%)", "walk_speed_coeff_likely")],
	[("Unknown_02", "unknown_02"), ("Unknown_03", "unknown_03"), ("Unknown_04", "unknown_04")],
	[("Unknown_09", "unknown_09"), ("Unknown_11", "unknown_11"), ("Unknown_12", "unknown_12")],
]


NUMERIC_FIELD_NAMES = [
	'hp',
	'attack',
	'defense',
	'speed',
	'boss_hp_multiplier',
	'boss_attack_multiplier',
	'boss_defense_multiplier',
	'boss_speed_override',
	'used_by_cboss_unknown_role',
	'boss_scalar_x1_or_x3',
	'a_size_or_physical_size',
	'walk_speed_coeff_likely',
	'unknown_02',
	'unknown_03',
	'unknown_04',
	'unknown_09',
	'unknown_11',
	'unknown_12',
]


PERCENT_FIELD_NAMES = {
	'boss_hp_multiplier',
	'boss_attack_multiplier',
	'boss_defense_multiplier',
	'a_size_or_physical_size',
	'walk_speed_coeff_likely',
}


MOVE_FIELD_NAMES = {'wild_move', 'wild_move_2nd_unused'}


def load_module_from_path(module_name, path):
	spec = importlib.util.spec_from_file_location(module_name, path)
	if spec is None or spec.loader is None:
		raise ImportError(f'Could not load module from {path}')
	module = importlib.util.module_from_spec(spec)
	spec.loader.exec_module(module)
	return module


def safe_int(value, default=None):
	try:
		return int(value)
	except Exception:
		return default


def move_symbol_to_label(symbol):
	if not isinstance(symbol, str):
		return str(symbol)
	text = symbol
	if text.startswith('MOVE_'):
		text = text[5:]
	return text.replace('_', ' ').title()


def species_symbol_to_label(symbol):
	if not isinstance(symbol, str):
		return str(symbol)
	text = symbol
	if text.startswith('SPECIES_'):
		text = text[8:]
	return text.replace('_', ' ').title()


class NumericSpinbox(tk.Spinbox):
	def __init__(self, master, textvariable, **kwargs):
		super().__init__(
			master,
			textvariable=textvariable,
			from_=-999999,
			to=999999,
			increment=1,
			width=12,
			justify='right',
			**kwargs,
		)


class PiiPropDataJSONGUI:
	def __init__(self, root):
		self.root = root
		self.root.title('PiiPropData JSON GUI')

		self.backend = load_module_from_path('pyPKCTool_PiiPropData_Backend', BACKEND_PATH)
		self.enumlib = load_module_from_path('pyPKCEnumLib_Backend', ENUM_LIB_PATH)

		self.format_version = getattr(self.backend, 'FORMAT_VERSION', 'piiprop-edit-json-2')
		self.species_id_to_symbol = getattr(self.backend, 'species_id_to_symbol', self._fallback_species_id_to_symbol)
		self.species_value_to_id = getattr(self.backend, 'species_value_to_id', self._fallback_species_value_to_id)
		self.normalize_number = getattr(self.backend, 'normalize_number', self._fallback_normalize_number)

		self.move_enum_members = list(self.enumlib.MoveEnum)
		self.move_id_to_symbol = {int(member.value): member.name for member in self.move_enum_members}
		self.move_symbol_to_id = {member.name: int(member.value) for member in self.move_enum_members}
		self.move_label_to_symbol = {}
		self.move_symbol_to_label_map = {}
		for member in self.move_enum_members:
			symbol = member.name
			label = move_symbol_to_label(symbol)
			self.move_label_to_symbol[label] = symbol
			self.move_symbol_to_label_map[symbol] = label
		self.move_option_labels = sorted(self.move_label_to_symbol.keys(), key=lambda value: value.casefold())

		self.json_path = None
		self.document = None
		self.current_index = None
		self.sidebar_order = []
		self.sidebar_item_to_index = {}
		self.sidebar_images = {}
		self.image_cache = {}
		self.sidebar_refresh_scheduled = False
		self._suppress_sidebar_select_event = False
		self._selecting_entry = False

		self.species_icon_image = None
		self.species_var = tk.StringVar()
		self.form_var = tk.StringVar()
		self.status_var = tk.StringVar(value='Open a PiiPropData JSON file to begin.')

		self.field_vars = {field_name: tk.StringVar() for field_name in NUMERIC_FIELD_NAMES}
		self.field_vars['wild_move'] = tk.StringVar()
		self.field_vars['wild_move_2nd_unused'] = tk.StringVar()

		self.build_ui()
		self.bind_dynamic_updates()

	def _fallback_species_id_to_symbol(self, species_id):
		return f'SPECIES_{int(species_id):04d}'

	def _fallback_species_value_to_id(self, value):
		if isinstance(value, int):
			return value
		if isinstance(value, str):
			text = value.strip()
			if text.startswith('SPECIES_'):
				remainder = text[8:]
				if remainder.isdigit():
					return int(remainder)
			return int(text)
		return int(value)

	def _fallback_normalize_number(self, value):
		if isinstance(value, bool):
			return int(value)
		if isinstance(value, int):
			return value
		if isinstance(value, float):
			if not math.isfinite(value):
				raise ValueError(f'Non-finite float: {value!r}')
			return value
		if isinstance(value, str):
			text = value.strip()
			if not text:
				raise ValueError('Empty value')
			if any(ch in text for ch in '.eE'):
				float_value = float(text)
				if float_value.is_integer() and '.' not in text and 'e' not in text.lower():
					return int(float_value)
				return float_value
			return int(text)
		raise ValueError(f'Unsupported numeric value: {value!r}')

	def build_ui(self):
		self.root.geometry('1450x900')
		self.root.minsize(1180, 760)

		topbar = ttk.Frame(self.root, padding=8)
		topbar.pack(side='top', fill='x')

		ttk.Button(topbar, text='Open JSON', command=self.open_json).pack(side='left')
		ttk.Button(topbar, text='Save JSON', command=self.save_json).pack(side='left', padx=(8, 0))
		ttk.Button(topbar, text='Save JSON As', command=self.save_json_as).pack(side='left', padx=(8, 0))
		ttk.Button(topbar, text='Rebuild PKC', command=self.rebuild_pkc).pack(side='left', padx=(8, 0))
		ttk.Button(topbar, text='Reload File', command=self.reload_json).pack(side='left', padx=(8, 0))

		self.file_label = ttk.Label(topbar, text='No file loaded')
		self.file_label.pack(side='left', padx=(16, 0))

		main = ttk.Panedwindow(self.root, orient='horizontal')
		main.pack(fill='both', expand=True, padx=8, pady=(0, 8))

		left_frame = ttk.Frame(main, padding=8)
		right_frame = ttk.Frame(main, padding=8)
		main.add(left_frame, weight=1)
		main.add(right_frame, weight=4)

		ttk.Label(left_frame, text='Entries').pack(anchor='w')

		self.sidebar_tree_style = 'PiiPropSidebar.Treeview'
		style = ttk.Style(self.root)
		style.configure(self.sidebar_tree_style, rowheight=SIDEBAR_ROWHEIGHT)

		self.entry_tree = ttk.Treeview(left_frame, show='tree', selectmode='browse', style=self.sidebar_tree_style)
		self.entry_tree.pack(side='left', fill='both', expand=True, pady=(6, 0))
		self.left_scroll = ttk.Scrollbar(left_frame, orient='vertical', command=self.on_sidebar_scrollbar)
		self.left_scroll.pack(side='left', fill='y', pady=(6, 0))
		self.entry_tree.configure(yscrollcommand=self.on_sidebar_treeview_scroll)
		self.entry_tree.bind('<<TreeviewSelect>>', self.on_entry_selected)
		self.entry_tree.bind('<Configure>', lambda _event: self.schedule_sidebar_visible_refresh())
		self.entry_tree.bind('<MouseWheel>', lambda _event: self.schedule_sidebar_visible_refresh())
		self.entry_tree.bind('<Button-4>', lambda _event: self.schedule_sidebar_visible_refresh())
		self.entry_tree.bind('<Button-5>', lambda _event: self.schedule_sidebar_visible_refresh())

		right_canvas = tk.Canvas(right_frame, highlightthickness=0)
		right_scroll = ttk.Scrollbar(right_frame, orient='vertical', command=right_canvas.yview)
		self.form_container = ttk.Frame(right_canvas)

		self.form_container.bind(
			'<Configure>',
			lambda event: right_canvas.configure(scrollregion=right_canvas.bbox('all')),
		)
		right_canvas.create_window((0, 0), window=self.form_container, anchor='nw')
		right_canvas.configure(yscrollcommand=right_scroll.set)

		right_canvas.pack(side='left', fill='both', expand=True)
		right_scroll.pack(side='left', fill='y')

		self.build_form(self.form_container)

		status_bar = ttk.Label(self.root, textvariable=self.status_var, anchor='w', relief='sunken')
		status_bar.pack(side='bottom', fill='x')

	def build_form(self, parent):
		row = 0

		header = ttk.Label(parent, text='PiiPropData JSON Editor', font=('TkDefaultFont', 12, 'bold'))
		header.grid(row=row, column=0, columnspan=8, sticky='w', pady=(0, 12))
		row += 1

		self.block_info_label = ttk.Label(parent, text='No entry selected')
		self.block_info_label.grid(row=row, column=0, columnspan=8, sticky='w', pady=(0, 12))
		row += 1

		self.species_icon_label = ttk.Label(parent)
		self.species_icon_label.grid(row=row, column=0, columnspan=2, sticky='w', pady=(0, 4))
		row += 1

		self._make_label(parent, 'Species ID', row, 0)
		self.species_spinbox = NumericSpinbox(parent, self.species_var)
		self.species_spinbox.grid(row=row, column=1, sticky='ew', padx=(0, 12), pady=2)

		self._make_label(parent, 'Form ID', row, 2)
		self.form_entry = ttk.Entry(parent, textvariable=self.form_var, width=12)
		self.form_entry.grid(row=row, column=3, sticky='ew', padx=(0, 12), pady=2)
		row += 1

		for field_group in FIELD_LAYOUT:
			row = self._make_field_row(parent, row, field_group)

		for column in range(8):
			parent.grid_columnconfigure(column, weight=1)

	def _make_label(self, parent, text, row, column):
		label = ttk.Label(parent, text=text)
		label.grid(row=row, column=column, sticky='w', padx=(0, 6), pady=2)

	def _make_field_row(self, parent, row, fields):
		for index, (label_text, field_name) in enumerate(fields):
			column = index * 2
			self._make_label(parent, label_text, row, column)
			if field_name in MOVE_FIELD_NAMES:
				widget = ttk.Combobox(
					parent,
					textvariable=self.field_vars[field_name],
					values=self.move_option_labels,
					state='readonly',
					width=28,
				)
			else:
				widget = NumericSpinbox(parent, self.field_vars[field_name])
			widget.grid(row=row, column=column + 1, sticky='ew', padx=(0, 12), pady=2)
		return row + 1

	def bind_dynamic_updates(self):
		self.species_var.trace_add('write', lambda *_: self.update_species_icon())
		self.form_var.trace_add('write', lambda *_: self.update_species_icon())

	def open_json(self):
		path = filedialog.askopenfilename(
			title='Open PiiPropData JSON',
			filetypes=[('JSON files', '*.json'), ('All files', '*.*')],
		)
		if not path:
			return
		self.load_json_file(path)

	def reload_json(self):
		if not self.json_path:
			return
		self.load_json_file(self.json_path)

	def load_json_file(self, path):
		try:
			with open(path, 'r', encoding='utf-8') as infile:
				document = json.load(infile)
			if document.get('format') != self.format_version:
				messagebox.showwarning(
					'Format mismatch',
					f"JSON format is {document.get('format')!r}, expected {self.format_version!r}. The editor will still try to load it.",
				)
			entries = document.get('entries')
			if not isinstance(entries, list):
				raise ValueError('JSON is missing an entries list')
		except Exception as exc:
			messagebox.showerror('Open failed', str(exc))
			return

		self.save_current_entry_to_document(silent=True)
		self.document = document
		self.json_path = path
		self.file_label.config(text=path)
		self.sidebar_images = {}
		self.image_cache = {}
		self.refresh_sidebar()
		self.status_var.set(f'Loaded {os.path.basename(path)} with {len(entries)} entries.')
		if self.sidebar_order:
			self.select_entry(self.sidebar_order[0])
		else:
			self.clear_form()

	def on_sidebar_scrollbar(self, *args):
		self.entry_tree.yview(*args)
		self.schedule_sidebar_visible_refresh()

	def on_sidebar_treeview_scroll(self, first, last):
		self.left_scroll.set(first, last)
		self.schedule_sidebar_visible_refresh()

	def schedule_sidebar_visible_refresh(self):
		if self.sidebar_refresh_scheduled:
			return
		self.sidebar_refresh_scheduled = True
		self.root.after_idle(self.refresh_visible_sidebar_icons)

	def refresh_visible_sidebar_icons(self):
		self.sidebar_refresh_scheduled = False
		if not self.document:
			return
		item_ids = list(self.entry_tree.get_children())
		if not item_ids:
			return
		height = max(1, int(self.entry_tree.winfo_height()))
		top_item = self.entry_tree.identify_row(0)
		bottom_item = self.entry_tree.identify_row(max(0, height - 1))
		if not top_item:
			visible_ids = item_ids[:min(len(item_ids), 24)]
		else:
			top_index = item_ids.index(top_item)
			if bottom_item:
				bottom_index = item_ids.index(bottom_item)
			else:
				bottom_index = min(len(item_ids) - 1, top_index + 12)
			start_index = max(0, top_index - 1)
			end_index = min(len(item_ids), bottom_index + 2)
			visible_ids = item_ids[start_index:end_index]
		visible_set = set(visible_ids)
		entries = self.document.get('entries', [])
		for item_id in visible_ids:
			if item_id in self.sidebar_images:
				continue
			actual_index = self.sidebar_item_to_index.get(item_id)
			if actual_index is None or not (0 <= actual_index < len(entries)):
				continue
			image = self.get_sidebar_icon_image(entries[actual_index])
			if image is not None:
				self.sidebar_images[item_id] = image
				self.entry_tree.item(item_id, image=image)
		for item_id in list(self.sidebar_images.keys()):
			if item_id in visible_set:
				continue
			self.entry_tree.item(item_id, image='')
			del self.sidebar_images[item_id]

	def build_sorted_entry_indexes(self):
		if not self.document:
			return []
		entries = self.document.get('entries', [])
		decorated = []
		for actual_index, entry in enumerate(entries):
			species_id, form_id = self.get_species_and_form_ids(entry)
			species_sort = species_id if species_id is not None else 10 ** 9
			if form_id in (None, ''):
				form_sort = 0
			else:
				try:
					form_sort = int(form_id)
				except Exception:
					form_sort = 10 ** 9
			name_sort = self.describe_entry(entry).casefold()
			decorated.append((species_sort, form_sort, name_sort, actual_index))
		decorated.sort()
		return [actual_index for _species_sort, _form_sort, _name_sort, actual_index in decorated]

	def refresh_sidebar(self):
		previous = self._suppress_sidebar_select_event
		self._suppress_sidebar_select_event = True
		try:
			for item_id in self.entry_tree.get_children():
				self.entry_tree.delete(item_id)
			self.sidebar_order = []
			self.sidebar_item_to_index = {}
			self.sidebar_images = {}
			if not self.document:
				return
			entries = self.document.get('entries', [])
			self.sidebar_order = self.build_sorted_entry_indexes()
			for position, actual_index in enumerate(self.sidebar_order):
				entry = entries[actual_index]
				item_id = f'entry_{position}'
				text = self.describe_entry(entry)
				self.entry_tree.insert('', 'end', iid=item_id, text=text)
				self.sidebar_item_to_index[item_id] = actual_index
		finally:
			self._suppress_sidebar_select_event = previous
		self.schedule_sidebar_visible_refresh()

	def describe_entry(self, entry):
		species_id, form_id = self.get_species_and_form_ids(entry)
		if species_id is None:
			base_name = 'Unknown Species'
			species_text = '?'
		else:
			base_name = species_symbol_to_label(self.species_id_to_symbol(species_id))
			species_text = str(int(species_id))
		display_form_id = 0 if form_id in (None, '') else int(form_id)
		return f'{species_text}, {display_form_id} - {base_name}'

	def on_entry_selected(self, _event=None):
		if self._suppress_sidebar_select_event or self._selecting_entry:
			return
		selection = self.entry_tree.selection()
		if not selection:
			return
		item_id = selection[0]
		index = self.sidebar_item_to_index.get(item_id)
		if index is None:
			return
		self.select_entry(index)

	def select_entry(self, index):
		if self.document is None:
			return
		entries = self.document.get('entries', [])
		if not (0 <= index < len(entries)):
			return
		if self._selecting_entry:
			return
		self._selecting_entry = True
		try:
			if self.current_index is not None and self.current_index != index:
				if not self.save_current_entry_to_document(silent=False):
					self.reselect_current_sidebar_item()
					return
			self.current_index = index
			self.select_sidebar_item_for_index(index)
			entry = entries[index]
			self.populate_form(entry)
		finally:
			self._selecting_entry = False

	def reselect_current_sidebar_item(self):
		if self.current_index is None:
			return
		self.select_sidebar_item_for_index(self.current_index)

	def select_sidebar_item_for_index(self, actual_index):
		for item_id, index in self.sidebar_item_to_index.items():
			if index == actual_index:
				current_selection = self.entry_tree.selection()
				if current_selection == (item_id,):
					self.entry_tree.focus(item_id)
					self.entry_tree.see(item_id)
					return
				previous = self._suppress_sidebar_select_event
				self._suppress_sidebar_select_event = True
				try:
					self.entry_tree.selection_set(item_id)
					self.entry_tree.focus(item_id)
					self.entry_tree.see(item_id)
				finally:
					self._suppress_sidebar_select_event = previous
				self.schedule_sidebar_visible_refresh()
				return

	def clear_form(self):
		self.current_index = None
		self.block_info_label.config(text='No entry selected')
		self.species_var.set('')
		self.form_var.set('0')
		for variable in self.field_vars.values():
			variable.set('')
		self.species_icon_label.configure(image='', text='')
		self.species_icon_image = None

	def infer_setter_for_entry(self, entry):
		setter = entry.get('setter')
		if isinstance(setter, str) and setter.strip():
			return setter
		_species_id, form_id = self.get_species_and_form_ids(entry)
		if form_id in (None, '', 0):
			return 'method_pprSetAllForAllFormno'
		return 'method_pprSetAll'

	def format_entry_info_text(self, entry, index=None):
		if index is None:
			index = self.current_index
		total = len(self.document.get('entries', [])) if isinstance(self.document, dict) else 0
		entry_label = '?' if index is None else str(int(index) + 1)
		setter = self.infer_setter_for_entry(entry)
		species_id, form_id = self.get_species_and_form_ids(entry)
		species_text = '?' if species_id is None else str(int(species_id))
		form_text = '0' if form_id in (None, '') else str(int(form_id))
		if total > 0:
			return f'Entry {entry_label}/{total} | Setter: {setter} | Species: {species_text} | Form: {form_text}'
		return f'Entry {entry_label} | Setter: {setter} | Species: {species_text} | Form: {form_text}'

	def populate_form(self, entry):
		species_id, form_id = self.get_species_and_form_ids(entry)
		self.species_var.set('' if species_id is None else str(species_id))
		self.form_var.set('' if form_id is None else str(form_id))

		fields = entry.get('fields', {})
		self.field_vars['hp'].set(self.format_numeric_display(fields.get('hp')))
		self.field_vars['attack'].set(self.format_numeric_display(fields.get('attack')))
		self.field_vars['defense'].set(self.format_numeric_display(fields.get('defense')))
		self.field_vars['speed'].set(self.format_numeric_display(fields.get('speed')))
		self.field_vars['boss_hp_multiplier'].set(self.format_numeric_display(fields.get('boss_hp_multiplier'), percent=True))
		self.field_vars['boss_attack_multiplier'].set(self.format_numeric_display(fields.get('boss_attack_multiplier'), percent=True))
		self.field_vars['boss_defense_multiplier'].set(self.format_numeric_display(fields.get('boss_defense_multiplier'), percent=True))
		self.field_vars['boss_speed_override'].set(self.format_numeric_display(fields.get('boss_speed_override')))
		self.field_vars['used_by_cboss_unknown_role'].set(self.format_numeric_display(fields.get('used_by_cboss_unknown_role')))
		self.field_vars['boss_scalar_x1_or_x3'].set(self.format_numeric_display(fields.get('boss_scalar_x1_or_x3')))
		self.field_vars['a_size_or_physical_size'].set(self.format_numeric_display(fields.get('a_size_or_physical_size'), percent=True))
		self.field_vars['walk_speed_coeff_likely'].set(self.format_numeric_display(fields.get('walk_speed_coeff_likely'), percent=True))
		self.field_vars['unknown_02'].set(self.format_numeric_display(fields.get('unknown_02')))
		self.field_vars['unknown_03'].set(self.format_numeric_display(fields.get('unknown_03')))
		self.field_vars['unknown_04'].set(self.format_numeric_display(fields.get('unknown_04')))
		self.field_vars['unknown_09'].set(self.format_numeric_display(fields.get('unknown_09')))
		self.field_vars['unknown_11'].set(self.format_numeric_display(fields.get('unknown_11')))
		self.field_vars['unknown_12'].set(self.format_numeric_display(fields.get('unknown_12')))

		wild_move_value = fields.get('wild_move')
		if wild_move_value is None and 'unknown_18' in fields:
			wild_move_value = fields.get('unknown_18')
		self.field_vars['wild_move'].set(self.move_value_to_label(wild_move_value))

		wild_move_2_value = fields.get('wild_move_2nd_unused')
		if wild_move_2_value is None and 'unknown_19' in fields:
			wild_move_2_value = fields.get('unknown_19')
		self.field_vars['wild_move_2nd_unused'].set(self.move_value_to_label(wild_move_2_value))

		self.block_info_label.config(text=self.format_entry_info_text(entry, self.current_index))
		self.update_species_icon()

	def format_numeric_display(self, value, percent=False):
		if value is None:
			return ''
		if percent:
			if isinstance(value, str):
				text = value.strip()
				if text.endswith('%'):
					return text[:-1].strip()
				return text
			return self.clean_number_text(value)
		return self.clean_number_text(value)

	def clean_number_text(self, value):
		if value is None:
			return ''
		if isinstance(value, str):
			return value.strip()
		if isinstance(value, int):
			return str(value)
		if isinstance(value, float):
			text = f'{value:.6f}'.rstrip('0').rstrip('.')
			if text in ('', '-0'):
				text = '0'
			return text
		return str(value)

	def get_species_and_form_ids(self, entry):
		species_value = entry.get('species')
		species_id = None
		if species_value is not None:
			try:
				species_id = int(self.species_value_to_id(species_value))
			except Exception:
				species_id = safe_int(species_value)
		form_value = entry.get('form')
		if form_value in (None, ''):
			form_id = 0
		else:
			form_id = safe_int(form_value, 0)
		return species_id, form_id

	def get_sidebar_icon_image(self, entry):
		species_id, form_id = self.get_species_and_form_ids(entry)
		icon_path = self.find_species_icon_path(species_id, form_id, kind='side')
		return self.load_icon_image(icon_path, max_dim=SIDEBAR_ICON_SIZE)

	def update_species_icon(self):
		species_text = self.species_var.get().strip()
		form_text = self.form_var.get().strip()
		if not species_text:
			self.species_icon_label.configure(image='', text='')
			self.species_icon_image = None
			return
		species_id = safe_int(species_text)
		form_id = None if not form_text else safe_int(form_text)
		icon_path = self.find_species_icon_path(species_id, form_id, kind='main')
		self.species_icon_image = self.load_icon_image(icon_path, max_dim=MAIN_ICON_SIZE)
		if self.species_icon_image is not None:
			self.species_icon_label.configure(image=self.species_icon_image, text='')
		else:
			self.species_icon_label.configure(image='', text=os.path.basename(icon_path))

	def find_species_icon_path(self, species_id, form_id=None, kind='main'):
		candidates = []
		if species_id is not None and form_id not in (None, ''):
			candidates.append(os.path.join(ICON_DIR, f'{species_id}_{form_id}_{kind}.png'))
		if species_id is not None:
			candidates.append(os.path.join(ICON_DIR, f'{species_id}_{kind}.png'))
		fallback = os.path.join(ICON_DIR, FALLBACK_ICON_NAME)
		candidates.append(fallback)
		for candidate in candidates:
			if os.path.isfile(candidate):
				return candidate
		return fallback

	def load_icon_image(self, path, max_dim, cache=True):
		cache_key = (path, int(max_dim))
		if cache and cache_key in self.image_cache:
			return self.image_cache[cache_key]
		if not os.path.isfile(path):
			if cache:
				self.image_cache[cache_key] = None
			return None
		try:
			image = tk.PhotoImage(file=path)
			width = image.width()
			height = image.height()
			if width > max_dim or height > max_dim:
				scale = max(1, math.ceil(max(width / max_dim, height / max_dim)))
				image = image.subsample(scale, scale)
			if cache:
				self.image_cache[cache_key] = image
			return image
		except Exception:
			if cache:
				self.image_cache[cache_key] = None
			return None

	def move_value_to_label(self, value):
		if value in (None, ''):
			return ''
		if isinstance(value, int):
			symbol = self.move_id_to_symbol.get(value)
			if symbol is not None:
				return self.move_symbol_to_label_map.get(symbol, move_symbol_to_label(symbol))
			return str(value)
		if isinstance(value, float) and value.is_integer():
			return self.move_value_to_label(int(value))
		if isinstance(value, str):
			text = value.strip()
			if text in self.move_symbol_to_label_map:
				return self.move_symbol_to_label_map[text]
			if text.startswith('MOVE_'):
				return move_symbol_to_label(text)
			return text
		return str(value)

	def move_label_to_value(self, label_text):
		text = label_text.strip()
		if not text:
			return ''
		symbol = self.move_label_to_symbol.get(text)
		if symbol is not None:
			return symbol
		return text

	def save_current_entry_to_document(self, silent=False):
		if self.document is None or self.current_index is None:
			return True
		entries = self.document.get('entries', [])
		if not (0 <= self.current_index < len(entries)):
			return True
		entry = entries[self.current_index]
		try:
			species_text = self.species_var.get().strip()
			if not species_text:
				raise ValueError('Species ID cannot be empty')
			species_id = int(self.normalize_number(species_text))
			form_text = self.form_var.get().strip()
			form_value = 0 if not form_text else int(self.normalize_number(form_text))

			entry['species'] = self.species_id_to_symbol(species_id)
			entry['form'] = form_value
			if not entry.get('setter'):
				entry['setter'] = 'method_pprSetAllForAllFormno' if form_value == 0 else 'method_pprSetAll'
			entry.pop('key', None)
			entry.pop('block_index', None)
			entry.pop('pc_range', None)

			fields = entry.setdefault('fields', {})
			fields['hp'] = self.parse_numeric_field('HP', self.field_vars['hp'].get(), percent=False)
			fields['attack'] = self.parse_numeric_field('ATK', self.field_vars['attack'].get(), percent=False)
			fields['defense'] = self.parse_numeric_field('DEF', self.field_vars['defense'].get(), percent=False)
			fields['speed'] = self.parse_numeric_field('SPE', self.field_vars['speed'].get(), percent=False)
			fields['boss_hp_multiplier'] = self.parse_numeric_field('Boss HP (%)', self.field_vars['boss_hp_multiplier'].get(), percent=True)
			fields['boss_attack_multiplier'] = self.parse_numeric_field('Boss ATK (%)', self.field_vars['boss_attack_multiplier'].get(), percent=True)
			fields['boss_defense_multiplier'] = self.parse_numeric_field('Boss DEF (%)', self.field_vars['boss_defense_multiplier'].get(), percent=True)
			fields['boss_speed_override'] = self.parse_numeric_field('Boss SPE', self.field_vars['boss_speed_override'].get(), percent=False)
			fields['used_by_cboss_unknown_role'] = self.parse_numeric_field('CBoss Role', self.field_vars['used_by_cboss_unknown_role'].get(), percent=False)
			fields['boss_scalar_x1_or_x3'] = self.parse_numeric_field('Boss Scalar X1/X3', self.field_vars['boss_scalar_x1_or_x3'].get(), percent=False)
			fields['wild_move'] = self.move_label_to_value(self.field_vars['wild_move'].get())
			fields['wild_move_2nd_unused'] = self.move_label_to_value(self.field_vars['wild_move_2nd_unused'].get())
			fields.pop('unknown_18', None)
			fields.pop('unknown_19', None)
			fields['a_size_or_physical_size'] = self.parse_numeric_field('Size Modifier (%)', self.field_vars['a_size_or_physical_size'].get(), percent=True)
			fields['walk_speed_coeff_likely'] = self.parse_numeric_field('Walk Speed (%)', self.field_vars['walk_speed_coeff_likely'].get(), percent=True)
			fields['unknown_02'] = self.parse_numeric_field('Unknown_02', self.field_vars['unknown_02'].get(), percent=False)
			fields['unknown_03'] = self.parse_numeric_field('Unknown_03', self.field_vars['unknown_03'].get(), percent=False)
			fields['unknown_04'] = self.parse_numeric_field('Unknown_04', self.field_vars['unknown_04'].get(), percent=False)
			fields['unknown_09'] = self.parse_numeric_field('Unknown_09', self.field_vars['unknown_09'].get(), percent=False)
			fields['unknown_11'] = self.parse_numeric_field('Unknown_11', self.field_vars['unknown_11'].get(), percent=False)
			fields['unknown_12'] = self.parse_numeric_field('Unknown_12', self.field_vars['unknown_12'].get(), percent=False)

			self.refresh_sidebar()
			self.select_sidebar_item_for_index(self.current_index)
			self.block_info_label.config(text=self.format_entry_info_text(entry, self.current_index))
			self.status_var.set(f'Updated entry {self.current_index + 1}.')
			return True
		except Exception as exc:
			if not silent:
				messagebox.showerror('Invalid value', str(exc))
			self.status_var.set(f'Could not apply form values: {exc}')
			return False

	def parse_numeric_field(self, label_text, raw_text, percent=False):
		text = raw_text.strip()
		if not text:
			raise ValueError(f'{label_text} cannot be empty')
		if percent:
			return f"{self.clean_number_text(self.normalize_number(text))}%"
		return self.normalize_number(text)

	def save_json(self):
		if self.document is None:
			return
		if not self.save_current_entry_to_document(silent=False):
			return
		path = self.json_path
		if not path:
			return self.save_json_as()
		try:
			with open(path, 'w', encoding='utf-8', newline='\n') as outfile:
				json.dump(self.document, outfile, ensure_ascii=False, indent='\t')
				outfile.write('\n')
			self.status_var.set(f'Saved {path}')
		except Exception as exc:
			messagebox.showerror('Save failed', str(exc))

	def save_json_as(self):
		if self.document is None:
			return
		if not self.save_current_entry_to_document(silent=False):
			return
		path = filedialog.asksaveasfilename(
			title='Save PiiPropData JSON',
			defaultextension='.json',
			filetypes=[('JSON files', '*.json'), ('All files', '*.*')],
			initialfile=os.path.basename(self.json_path) if self.json_path else 'PiiPropData.json',
		)
		if not path:
			return
		self.json_path = path
		self.file_label.config(text=path)
		self.save_json()

	def build_backend_compatible_document(self, source_pkc):
		working = copy.deepcopy(self.document)
		if not isinstance(working, dict):
			raise ValueError('Current JSON document is not an object')
		entries = working.get('entries')
		if not isinstance(entries, list):
			raise ValueError('Current JSON document is missing an entries list')

		backend_entries = None
		build_export = getattr(self.backend, 'build_export_document', None)
		if callable(build_export):
			backend_export = build_export(source_pkc)
			backend_entries = backend_export.get('entries') if isinstance(backend_export, dict) else None
		if not isinstance(backend_entries, list):
			backend_entries = []

		working['format'] = getattr(self.backend, 'FORMAT_VERSION', working.get('format'))
		compat_entries = []
		for entry_index, entry in enumerate(entries):
			if not isinstance(entry, dict):
				raise ValueError(f'Entry at index {entry_index} is not an object')
			compat_entry = copy.deepcopy(entry)
			species_id, form_id = self.get_species_and_form_ids(entry)
			if species_id is None:
				raise ValueError(f'Entry at index {entry_index} is missing a valid species value')
			compat_entry['species'] = self.species_id_to_symbol(species_id)
			setter = compat_entry.get('setter') or ('method_pprSetAllForAllFormno' if form_id in (None, '', 0) else 'method_pprSetAll')
			compat_entry['setter'] = setter

			if entry_index < len(backend_entries):
				backend_entry = backend_entries[entry_index]
				if isinstance(backend_entry, dict) and 'block_index' in backend_entry:
					compat_entry['block_index'] = backend_entry.get('block_index')
			else:
				compat_entry.pop('block_index', None)

			if setter == 'method_pprSetAllForAllFormno' and form_id in (None, '', 0):
				compat_entry['form'] = None
			else:
				compat_entry['form'] = 0 if form_id in (None, '') else int(form_id)

			compat_form = compat_entry.get('form')
			compat_entry['key'] = compat_entry['species'] if compat_form in (None, '') else f"{compat_entry['species']},{int(compat_form)}"
			compat_entries.append(compat_entry)

		working['entries'] = compat_entries
		return working

	def rebuild_pkc(self):
		if self.document is None:
			return
		if not self.save_current_entry_to_document(silent=False):
			return

		source_pkc = filedialog.askopenfilename(
			title='Select original PiiPropData PKC',
			filetypes=[('PKC files', '*.pkc'), ('All files', '*.*')],
		)
		if not source_pkc:
			return

		default_output = os.path.splitext(self.json_path or source_pkc)[0] + '.rebuilt.pkc'
		output_pkc = filedialog.asksaveasfilename(
			title='Save rebuilt PKC',
			defaultextension='.pkc',
			filetypes=[('PKC files', '*.pkc'), ('All files', '*.*')],
			initialfile=os.path.basename(default_output),
		)
		if not output_pkc:
			return

		try:
			pkc_document = self.backend.decode_pkc(source_pkc)
			edit_document = self.build_backend_compatible_document(source_pkc)
			updated_document = self.backend.apply_json_to_document(pkc_document, edit_document)
			pkc_bytes = self.backend.encode_pkc_document(updated_document)
			with open(output_pkc, 'wb') as outfile:
				outfile.write(pkc_bytes)
			self.status_var.set(f'Rebuilt PKC written to {output_pkc}')
			messagebox.showinfo('Rebuild complete', f'Wrote {output_pkc}')
		except Exception as exc:
			messagebox.showerror('Rebuild failed', str(exc))
			self.status_var.set(f'Rebuild failed: {exc}')


def main():
	if not os.path.isfile(BACKEND_PATH):
		messagebox.showerror('Missing backend', f'Could not find {BACKEND_PATH}')
		return
	if not os.path.isfile(ENUM_LIB_PATH):
		messagebox.showerror('Missing enum library', f'Could not find {ENUM_LIB_PATH}')
		return

	root = tk.Tk()
	app = PiiPropDataJSONGUI(root)
	if len(sys.argv) > 1:
		path = sys.argv[1]
		if os.path.isfile(path):
			app.load_json_file(path)
	root.mainloop()


if __name__ == '__main__':
	main()
