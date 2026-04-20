#!/usr/bin/env python3
import copy
import importlib.util
import json
import math
import os
import random
import sys
import tkinter as tk
from tkinter import filedialog, messagebox, ttk


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ICON_DIR = os.path.join(SCRIPT_DIR, 'icon')
BACKEND_PATH = os.path.join(SCRIPT_DIR, 'pyPKCTool.StageDescData.py')
ENUM_LIB_PATH = os.path.join(SCRIPT_DIR, 'pyPKCEnumLib.py')
FALLBACK_ICON_NAME = '_fallback.png'
MAIN_ICON_SUFFIX = '_main'


DROPDOWN_WIDTH = 24
SPECIES_ICON_PX = 64


class WeightSpinbox(tk.Spinbox):
	def __init__(self, master, textvariable, **kwargs):
		super().__init__(
			master,
			textvariable=textvariable,
			from_=0,
			to=100,
			increment=1,
			width=10,
			justify='right',
			**kwargs,
		)


class StageDescDataJSONGUI:
	def __init__(self, root):
		self.root = root
		self.root.title('StageDescData JSON GUI')
		self.root.geometry('1500x860')
		self.root.minsize(1200, 760)

		self.backend = self.load_module_from_path('pyPKCTool_StageDescData_Backend', BACKEND_PATH)
		self.enumlib = self.load_module_from_path('pyPKCEnumLib_StageDesc_Backend', ENUM_LIB_PATH)
		self.format_version = getattr(self.backend, 'FORMAT_VERSION', 'stagedesc-edit-json-1')

		self.document = None
		self.json_path = None
		self.image_cache = {}
		self.primary_icon_image = None
		self.secondary_icon_image = None
		self.tertiary_icon_image = None
		self._updating_ui = False
		self._randomizer_window = None

		self.difficulty_members = list(self.enumlib.DifficultyEnum)
		self.rank_members = list(self.enumlib.RankEnum)
		self.stage_members = list(self.enumlib.StageEnum)
		self.species_members = list(self.enumlib.SpeciesEnum)

		self.difficulty_symbol_to_label = {member.name: self.difficulty_symbol_to_label_text(member.name) for member in self.difficulty_members}
		self.rank_symbol_to_label = {member.name: self.rank_symbol_to_label_text(member.name) for member in self.rank_members}
		self.stage_symbol_to_label = {member.name: self.stage_symbol_to_label_text(member.name) for member in self.stage_members}
		self.species_symbol_to_label = {member.name: self.species_symbol_to_label_text(member.name) for member in self.species_members}

		self.difficulty_label_to_symbol = {label: symbol for symbol, label in self.difficulty_symbol_to_label.items()}
		self.rank_label_to_symbol = {label: symbol for symbol, label in self.rank_symbol_to_label.items()}
		self.stage_label_to_symbol = {label: symbol for symbol, label in self.stage_symbol_to_label.items()}
		self.species_label_to_symbol = {label: symbol for symbol, label in self.species_symbol_to_label.items()}

		self.difficulty_labels = [self.difficulty_symbol_to_label[member.name] for member in self.difficulty_members]
		self.rank_labels = [self.rank_symbol_to_label[member.name] for member in self.rank_members]
		self.stage_labels = [self.stage_symbol_to_label[member.name] for member in self.stage_members]

		species_labels = []
		for member in self.species_members:
			label = self.species_symbol_to_label[member.name]
			if member.name == 'SPECIES_NONE':
				species_labels.append((0, label))
			else:
				species_labels.append((1, label))
		species_labels.sort(key=lambda item: (item[0], item[1].casefold()))
		self.species_option_labels = [label for _priority, label in species_labels]

		self.status_var = tk.StringVar(value='Open a StageDescData JSON file to begin.')
		self.file_label_var = tk.StringVar(value='No file loaded')
		self.info_var = tk.StringVar(value='No selection')

		self.difficulty_var = tk.StringVar()
		self.rank_var = tk.StringVar()
		self.level_var = tk.StringVar()
		self.subarea_var = tk.StringVar()
		self.spawn_point_var = tk.StringVar()
		self.spawn_list_var = tk.StringVar()
		self.autoweight_var = tk.BooleanVar(value=False)
		self.weight_var = tk.StringVar()

		self.primary_species_var = tk.StringVar()
		self.secondary_species_var = tk.StringVar()
		self.tertiary_species_var = tk.StringVar()

		self.current_group_option = None
		self.current_map_entry_option = None
		self.current_row_option = None
		self.group_options = []
		self.map_entry_options = []
		self.row_options = []

		self.build_ui()
		self.bind_events()

	def load_module_from_path(self, module_name, path):
		spec = importlib.util.spec_from_file_location(module_name, path)
		if spec is None or spec.loader is None:
			raise ImportError(f'Could not load module from {path}')
		module = importlib.util.module_from_spec(spec)
		spec.loader.exec_module(module)
		return module

	def difficulty_symbol_to_label_text(self, symbol):
		text = str(symbol)
		if text == 'DIFFICULTY_EX':
			return 'EX'
		if text.startswith('DIFFICULTY_'):
			text = text[11:]
		return text.replace('_', ' ').title()

	def rank_symbol_to_label_text(self, symbol):
		text = str(symbol)
		if text.startswith('RANK_'):
			text = text[5:]
		if text == 'GX':
			return 'GX'
		return text

	def stage_symbol_to_label_text(self, symbol):
		text = str(symbol)
		if text.startswith('MAP_'):
			text = text[4:]
		return text.replace('_', ' ').title()

	def species_symbol_to_label_text(self, symbol):
		text = str(symbol)
		if text.startswith('SPECIES_'):
			text = text[8:]
		return text.replace('_', ' ').title()

	def label_to_species_symbol(self, label):
		return self.species_label_to_symbol.get(label.strip(), 'SPECIES_NONE')

	def symbol_to_species_label(self, symbol):
		if symbol in self.species_symbol_to_label:
			return self.species_symbol_to_label[symbol]
		return self.species_symbol_to_label_text(symbol)

	def build_ui(self):
		topbar = ttk.Frame(self.root, padding=8)
		topbar.pack(side='top', fill='x')

		ttk.Button(topbar, text='Open JSON', command=self.open_json).pack(side='left')
		ttk.Button(topbar, text='Save JSON', command=self.save_json).pack(side='left', padx=(8, 0))
		ttk.Button(topbar, text='Save JSON As', command=self.save_json_as).pack(side='left', padx=(8, 0))
		ttk.Button(topbar, text='Rebuild PKC', command=self.rebuild_pkc).pack(side='left', padx=(8, 0))
		ttk.Button(topbar, text='Reload File', command=self.reload_json).pack(side='left', padx=(8, 0))
		ttk.Button(topbar, text='Randomizer Options', command=self.open_randomizer_options).pack(side='left', padx=(8, 0))

		ttk.Label(topbar, textvariable=self.file_label_var).pack(side='left', padx=(16, 0))

		container = ttk.Frame(self.root, padding=8)
		container.pack(fill='both', expand=True)

		header = ttk.Label(container, text='StageDescData JSON Editor', font=('TkDefaultFont', 12, 'bold'))
		header.grid(row=0, column=0, columnspan=8, sticky='w', pady=(0, 8))

		info_label = ttk.Label(container, textvariable=self.info_var)
		info_label.grid(row=1, column=0, columnspan=8, sticky='w', pady=(0, 14))

		self.make_label(container, 'Difficulty', 2, 0)
		self.difficulty_combo = ttk.Combobox(container, textvariable=self.difficulty_var, state='readonly', width=DROPDOWN_WIDTH, values=self.difficulty_labels)
		self.difficulty_combo.grid(row=2, column=1, sticky='ew', padx=(0, 12), pady=2)

		self.make_label(container, 'Rank', 2, 2)
		self.rank_combo = ttk.Combobox(container, textvariable=self.rank_var, state='readonly', width=DROPDOWN_WIDTH, values=self.rank_labels)
		self.rank_combo.grid(row=2, column=3, sticky='ew', padx=(0, 12), pady=2)

		self.make_label(container, 'Level', 2, 4)
		self.level_combo = ttk.Combobox(container, textvariable=self.level_var, state='readonly', width=DROPDOWN_WIDTH, values=self.stage_labels)
		self.level_combo.grid(row=2, column=5, sticky='ew', padx=(0, 12), pady=2)

		self.make_label(container, 'SubArea', 3, 0)
		self.subarea_combo = ttk.Combobox(container, textvariable=self.subarea_var, state='readonly', width=DROPDOWN_WIDTH + 8)
		self.subarea_combo.grid(row=3, column=1, columnspan=2, sticky='ew', padx=(0, 12), pady=2)

		self.make_label(container, 'Spawn Point', 3, 3)
		self.spawn_point_combo = ttk.Combobox(container, textvariable=self.spawn_point_var, state='readonly', width=DROPDOWN_WIDTH + 8)
		self.spawn_point_combo.grid(row=3, column=4, columnspan=2, sticky='ew', padx=(0, 12), pady=2)

		self.make_label(container, 'SpawnLists', 4, 0)
		self.spawn_list_combo = ttk.Combobox(container, textvariable=self.spawn_list_var, state='readonly', width=DROPDOWN_WIDTH + 8)
		self.spawn_list_combo.grid(row=4, column=1, columnspan=2, sticky='ew', padx=(0, 12), pady=2)

		self.make_label(container, 'Weight (%)', 4, 3)
		self.weight_spinbox = WeightSpinbox(container, self.weight_var)
		self.weight_spinbox.grid(row=4, column=4, sticky='w', padx=(0, 12), pady=2)

		self.autoweight_checkbox = ttk.Checkbutton(container, text='Autoweigh all spawn lists', variable=self.autoweight_var, command=self.on_autoweight_toggled)
		self.autoweight_checkbox.grid(row=4, column=5, columnspan=3, sticky='w', pady=2)

		spacer = ttk.Label(container, text='')
		spacer.grid(row=5, column=0, columnspan=8, pady=(4, 8))

		self.primary_icon_label = ttk.Label(container)
		self.primary_icon_label.grid(row=6, column=0, sticky='w', padx=(0, 8), pady=4)
		self.primary_combo = ttk.Combobox(container, textvariable=self.primary_species_var, state='readonly', width=40, values=self.species_option_labels)
		self.primary_combo.grid(row=6, column=1, columnspan=5, sticky='ew', padx=(0, 12), pady=4)

		self.secondary_icon_label = ttk.Label(container)
		self.secondary_icon_label.grid(row=7, column=0, sticky='w', padx=(0, 8), pady=4)
		self.secondary_combo = ttk.Combobox(container, textvariable=self.secondary_species_var, state='readonly', width=40, values=self.species_option_labels)
		self.secondary_combo.grid(row=7, column=1, columnspan=5, sticky='ew', padx=(0, 12), pady=4)

		self.tertiary_icon_label = ttk.Label(container)
		self.tertiary_icon_label.grid(row=8, column=0, sticky='w', padx=(0, 8), pady=4)
		self.tertiary_combo = ttk.Combobox(container, textvariable=self.tertiary_species_var, state='readonly', width=40, values=self.species_option_labels)
		self.tertiary_combo.grid(row=8, column=1, columnspan=5, sticky='ew', padx=(0, 12), pady=4)

		for column in range(8):
			container.grid_columnconfigure(column, weight=1)

		status_bar = ttk.Label(self.root, textvariable=self.status_var, anchor='w', relief='sunken')
		status_bar.pack(side='bottom', fill='x')

	def make_label(self, parent, text, row, column):
		ttk.Label(parent, text=text).grid(row=row, column=column, sticky='w', padx=(0, 6), pady=2)

	def bind_events(self):
		self.difficulty_combo.bind('<<ComboboxSelected>>', self.on_filter_changed)
		self.rank_combo.bind('<<ComboboxSelected>>', self.on_filter_changed)
		self.level_combo.bind('<<ComboboxSelected>>', self.on_filter_changed)
		self.subarea_combo.bind('<<ComboboxSelected>>', self.on_subarea_changed)
		self.spawn_point_combo.bind('<<ComboboxSelected>>', self.on_spawn_point_changed)
		self.spawn_list_combo.bind('<<ComboboxSelected>>', self.on_spawn_list_changed)
		self.primary_combo.bind('<<ComboboxSelected>>', lambda _event: self.update_spawn_icons())
		self.secondary_combo.bind('<<ComboboxSelected>>', lambda _event: self.update_spawn_icons())
		self.tertiary_combo.bind('<<ComboboxSelected>>', lambda _event: self.update_spawn_icons())

	def open_json(self):
		path = filedialog.askopenfilename(
			title='Open StageDescData JSON',
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
				messagebox.showwarning('Format mismatch', f"JSON format is {document.get('format')!r}, expected {self.format_version!r}. The editor will still try to load it.")
			entries = document.get('entries')
			if not isinstance(entries, list):
				raise ValueError('JSON is missing an entries list')
		except Exception as exc:
			messagebox.showerror('Open failed', str(exc))
			return

		self.document = document
		self.json_path = path
		self.file_label_var.set(path)
		self.status_var.set(f"Loaded {os.path.basename(path)} with {len(entries)} entries.")
		self.initialize_selection_from_document()

	def initialize_selection_from_document(self):
		if not self.document or not self.document.get('entries'):
			self.clear_form()
			return
		first_target = None
		for entry in self.document['entries']:
			targets = entry.get('targets')
			if isinstance(targets, list) and targets:
				first_target = targets[0]
				break
		if first_target is None:
			self.clear_form()
			return
		self._updating_ui = True
		try:
			self.difficulty_combo['values'] = self.difficulty_labels
			self.rank_combo['values'] = self.rank_labels
			self.level_combo['values'] = self.stage_labels
			self.difficulty_var.set(self.difficulty_symbol_to_label.get(first_target.get('difficulty'), self.difficulty_labels[0]))
			self.rank_var.set(self.rank_symbol_to_label.get(first_target.get('rank'), self.rank_labels[0]))
			self.level_var.set(self.stage_symbol_to_label.get(first_target.get('stage_type'), self.stage_labels[0]))
		finally:
			self._updating_ui = False
		self.rebuild_selection_options()

	def clear_form(self):
		self.current_group_option = None
		self.current_map_entry_option = None
		self.current_row_option = None
		self.group_options = []
		self.map_entry_options = []
		self.row_options = []
		self._updating_ui = True
		try:
			self.subarea_combo['values'] = []
			self.spawn_point_combo['values'] = []
			self.spawn_list_combo['values'] = []
			self.subarea_var.set('')
			self.spawn_point_var.set('')
			self.spawn_list_var.set('')
			self.weight_var.set('')
			self.primary_species_var.set('')
			self.secondary_species_var.set('')
			self.tertiary_species_var.set('')
		finally:
			self._updating_ui = False
		self.primary_icon_label.configure(image='', text='')
		self.secondary_icon_label.configure(image='', text='')
		self.tertiary_icon_label.configure(image='', text='')
		self.primary_icon_image = None
		self.secondary_icon_image = None
		self.tertiary_icon_image = None
		self.info_var.set('No selection')
		self.update_weight_widget_state()

	def current_filter_symbols(self):
		difficulty_symbol = self.difficulty_label_to_symbol.get(self.difficulty_var.get().strip())
		rank_symbol = self.rank_label_to_symbol.get(self.rank_var.get().strip())
		stage_symbol = self.stage_label_to_symbol.get(self.level_var.get().strip())
		return difficulty_symbol, rank_symbol, stage_symbol

	def entry_matches_filters(self, entry, difficulty_symbol, rank_symbol, stage_symbol):
		targets = entry.get('targets', [])
		for target in targets:
			if not isinstance(target, dict):
				continue
			if target.get('difficulty') == difficulty_symbol and target.get('rank') == rank_symbol and target.get('stage_type') == stage_symbol:
				return True
		return False

	def build_group_options(self):
		if not self.document:
			return []
		difficulty_symbol, rank_symbol, stage_symbol = self.current_filter_symbols()
		if not difficulty_symbol or not rank_symbol or not stage_symbol:
			return []
		options = []
		for entry in self.document.get('entries', []):
			if not self.entry_matches_filters(entry, difficulty_symbol, rank_symbol, stage_symbol):
				continue
			fields = entry.get('fields', {}) if isinstance(entry.get('fields'), dict) else {}
			map_desc_id = fields.get('map_desc_id')
			group_index = entry.get('group_index')
			label = f"MapDesc {self.clean_number_text(map_desc_id)} - Group {group_index}"
			options.append({
				'label': label,
				'entry': entry,
				'group_index': group_index,
				'map_desc_id': map_desc_id,
			})
		options.sort(key=lambda item: (self.sortable_number(item['map_desc_id']), self.sortable_number(item['group_index']), item['label'].casefold()))
		return options

	def build_map_entry_options(self, group_option):
		if not group_option:
			return []
		entry = group_option['entry']
		options = []
		for index, map_entry in enumerate(entry.get('map_desc_entries', [])):
			entry_index = map_entry.get('entry_index', index)
			label = f"Spawn Point {index + 1} - Entry {entry_index}"
			options.append({
				'label': label,
				'map_entry': map_entry,
				'index': index,
			})
		return options

	def build_row_options(self, map_entry_option):
		if not map_entry_option:
			return []
		map_entry = map_entry_option['map_entry']
		options = []
		for index, row in enumerate(map_entry.get('enemy_rows', [])):
			row_index = row.get('row_index', index)
			label = f"SpawnList {index + 1} - Row {self.clean_number_text(row_index)}"
			options.append({
				'label': label,
				'row': row,
				'index': index,
			})
		return options

	def rebuild_selection_options(self):
		if self.document is None:
			self.clear_form()
			return
		if not self.save_current_row_to_document(silent=True):
			return

		previous_group_label = self.subarea_var.get().strip()
		previous_map_label = self.spawn_point_var.get().strip()
		previous_row_label = self.spawn_list_var.get().strip()

		self.group_options = self.build_group_options()
		group_labels = [option['label'] for option in self.group_options]
		self._updating_ui = True
		try:
			self.subarea_combo['values'] = group_labels
			if previous_group_label in group_labels:
				self.subarea_var.set(previous_group_label)
			else:
				self.subarea_var.set(group_labels[0] if group_labels else '')
		finally:
			self._updating_ui = False

		self.current_group_option = self.find_option_by_label(self.group_options, self.subarea_var.get().strip())
		self.map_entry_options = self.build_map_entry_options(self.current_group_option)
		map_labels = [option['label'] for option in self.map_entry_options]
		self._updating_ui = True
		try:
			self.spawn_point_combo['values'] = map_labels
			if previous_map_label in map_labels:
				self.spawn_point_var.set(previous_map_label)
			else:
				self.spawn_point_var.set(map_labels[0] if map_labels else '')
		finally:
			self._updating_ui = False

		self.current_map_entry_option = self.find_option_by_label(self.map_entry_options, self.spawn_point_var.get().strip())
		self.row_options = self.build_row_options(self.current_map_entry_option)
		row_labels = [option['label'] for option in self.row_options]
		self._updating_ui = True
		try:
			self.spawn_list_combo['values'] = row_labels
			if previous_row_label in row_labels:
				self.spawn_list_var.set(previous_row_label)
			else:
				self.spawn_list_var.set(row_labels[0] if row_labels else '')
		finally:
			self._updating_ui = False

		self.current_row_option = self.find_option_by_label(self.row_options, self.spawn_list_var.get().strip())
		self.refresh_current_row_display()

	def refresh_current_row_display(self):
		self.current_group_option = self.find_option_by_label(self.group_options, self.subarea_var.get().strip())
		self.current_map_entry_option = self.find_option_by_label(self.map_entry_options, self.spawn_point_var.get().strip())
		self.current_row_option = self.find_option_by_label(self.row_options, self.spawn_list_var.get().strip())

		if self.current_group_option is None or self.current_map_entry_option is None or self.current_row_option is None:
			self.clear_spawn_editors_only()
			return

		row = self.current_row_option['row']
		entry = self.current_group_option['entry']
		map_entry = self.current_map_entry_option['map_entry']
		self._updating_ui = True
		try:
			self.primary_species_var.set(self.symbol_to_species_label(row.get('species_primary', 'SPECIES_NONE')))
			self.secondary_species_var.set(self.symbol_to_species_label(self.normalize_nullable_species(row.get('species_secondary'))))
			self.tertiary_species_var.set(self.symbol_to_species_label(self.normalize_nullable_species(row.get('species_tertiary'))))
			self.weight_var.set(self.clean_number_text(row.get('weight')))
		finally:
			self._updating_ui = False

		self.info_var.set(
			f"Group {entry.get('group_index')} | MapDesc {self.clean_number_text(entry.get('fields', {}).get('map_desc_id'))} | "
			f"Spawn Point {self.current_map_entry_option['index'] + 1} | SpawnList {self.current_row_option['index'] + 1} | "
			f"Map Entry {map_entry.get('entry_index')}"
		)
		if self.autoweight_var.get():
			self.apply_autoweight_to_current_spawn_point(update_display=False)
			self._updating_ui = True
			try:
				self.weight_var.set(self.clean_number_text(self.current_row_option['row'].get('weight')))
			finally:
				self._updating_ui = False
		self.update_weight_widget_state()
		self.update_spawn_icons()

	def clear_spawn_editors_only(self):
		self._updating_ui = True
		try:
			self.primary_species_var.set('')
			self.secondary_species_var.set('')
			self.tertiary_species_var.set('')
			self.weight_var.set('')
		finally:
			self._updating_ui = False
		self.primary_icon_label.configure(image='', text='')
		self.secondary_icon_label.configure(image='', text='')
		self.tertiary_icon_label.configure(image='', text='')
		self.primary_icon_image = None
		self.secondary_icon_image = None
		self.tertiary_icon_image = None
		self.info_var.set('No selection')
		self.update_weight_widget_state()

	def find_option_by_label(self, options, label):
		for option in options:
			if option['label'] == label:
				return option
		return None

	def on_filter_changed(self, _event=None):
		if self._updating_ui:
			return
		if not self.save_current_row_to_document(silent=False):
			return
		self.rebuild_selection_options()

	def on_subarea_changed(self, _event=None):
		if self._updating_ui:
			return
		if not self.save_current_row_to_document(silent=False):
			return
		previous_map_label = self.spawn_point_var.get().strip()
		previous_row_label = self.spawn_list_var.get().strip()
		self.current_group_option = self.find_option_by_label(self.group_options, self.subarea_var.get().strip())
		self.map_entry_options = self.build_map_entry_options(self.current_group_option)
		map_labels = [option['label'] for option in self.map_entry_options]
		self._updating_ui = True
		try:
			self.spawn_point_combo['values'] = map_labels
			self.spawn_point_var.set(previous_map_label if previous_map_label in map_labels else (map_labels[0] if map_labels else ''))
		finally:
			self._updating_ui = False
		self.current_map_entry_option = self.find_option_by_label(self.map_entry_options, self.spawn_point_var.get().strip())
		self.row_options = self.build_row_options(self.current_map_entry_option)
		row_labels = [option['label'] for option in self.row_options]
		self._updating_ui = True
		try:
			self.spawn_list_combo['values'] = row_labels
			self.spawn_list_var.set(previous_row_label if previous_row_label in row_labels else (row_labels[0] if row_labels else ''))
		finally:
			self._updating_ui = False
		self.current_row_option = self.find_option_by_label(self.row_options, self.spawn_list_var.get().strip())
		self.refresh_current_row_display()

	def on_spawn_point_changed(self, _event=None):
		if self._updating_ui:
			return
		if not self.save_current_row_to_document(silent=False):
			return
		previous_row_label = self.spawn_list_var.get().strip()
		self.current_map_entry_option = self.find_option_by_label(self.map_entry_options, self.spawn_point_var.get().strip())
		self.row_options = self.build_row_options(self.current_map_entry_option)
		row_labels = [option['label'] for option in self.row_options]
		self._updating_ui = True
		try:
			self.spawn_list_combo['values'] = row_labels
			self.spawn_list_var.set(previous_row_label if previous_row_label in row_labels else (row_labels[0] if row_labels else ''))
		finally:
			self._updating_ui = False
		self.current_row_option = self.find_option_by_label(self.row_options, self.spawn_list_var.get().strip())
		self.refresh_current_row_display()

	def on_spawn_list_changed(self, _event=None):
		if self._updating_ui:
			return
		if not self.save_current_row_to_document(silent=False):
			return
		self.current_row_option = self.find_option_by_label(self.row_options, self.spawn_list_var.get().strip())
		self.refresh_current_row_display()

	def update_weight_widget_state(self):
		state = 'disabled' if self.autoweight_var.get() else 'normal'
		try:
			self.weight_spinbox.configure(state=state)
		except Exception:
			pass

	def on_autoweight_toggled(self):
		if self.document is None:
			return
		if self.autoweight_var.get():
			self.apply_autoweight_to_current_spawn_point(update_display=True)
		self.update_weight_widget_state()

	def apply_autoweight_to_current_spawn_point(self, update_display=True):
		if self.current_map_entry_option is None:
			return
		rows = self.current_map_entry_option['map_entry'].get('enemy_rows', [])
		if not rows:
			return
		weights = self.compute_equal_weights(len(rows))
		for row, weight in zip(rows, weights):
			row['weight'] = weight
		map_desc_id = self.current_map_entry_option['map_entry'].get('map_desc_id', self.current_group_option.get('map_desc_id') if self.current_group_option else None)
		self.propagate_shared_map_desc_entries(map_desc_id, self.current_group_option['entry'].get('map_desc_entries', []))
		if update_display and self.current_row_option is not None:
			self._updating_ui = True
			try:
				self.weight_var.set(self.clean_number_text(self.current_row_option['row'].get('weight')))
			finally:
				self._updating_ui = False
			self.status_var.set('Applied equal weighting to the current spawn point.')

	def compute_equal_weights(self, count):
		if count <= 0:
			return []
		if count == 1:
			return [100]
		base = round(100.0 / count, 6)
		weights = [base for _ in range(count - 1)]
		last = round(100.0 - sum(weights), 6)
		weights.append(last)
		return [self.normalize_weight_number(weight) for weight in weights]

	def normalize_weight_number(self, value):
		if isinstance(value, float):
			if value.is_integer():
				return int(value)
			return round(value, 6)
		return value

	def normalize_nullable_species(self, value):
		if value in (None, ''):
			return 'SPECIES_NONE'
		return value

	def current_map_desc_id(self):
		if self.current_group_option is None:
			return None
		entry = self.current_group_option['entry']
		fields = entry.get('fields', {}) if isinstance(entry.get('fields'), dict) else {}
		return fields.get('map_desc_id')

	def save_current_row_to_document(self, silent=False):
		if self.document is None or self.current_group_option is None or self.current_map_entry_option is None or self.current_row_option is None:
			return True
		try:
			row = self.current_row_option['row']
			row['species_primary'] = self.label_to_species_symbol(self.primary_species_var.get())
			secondary_symbol = self.label_to_species_symbol(self.secondary_species_var.get())
			tertiary_symbol = self.label_to_species_symbol(self.tertiary_species_var.get())
			row['species_secondary'] = None if secondary_symbol == 'SPECIES_NONE' else secondary_symbol
			row['species_tertiary'] = None if tertiary_symbol == 'SPECIES_NONE' else tertiary_symbol
			if self.autoweight_var.get():
				self.apply_autoweight_to_current_spawn_point(update_display=False)
			else:
				weight_text = self.weight_var.get().strip()
				if not weight_text:
					raise ValueError('Weight (%) cannot be empty')
				weight_value = self.backend.normalize_number(weight_text)
				row['weight'] = self.normalize_weight_number(weight_value)
			map_desc_id = self.current_map_desc_id()
			self.propagate_shared_map_desc_entries(map_desc_id, self.current_group_option['entry'].get('map_desc_entries', []))
			return True
		except Exception as exc:
			if not silent:
				messagebox.showerror('Invalid value', str(exc))
			self.status_var.set(f'Could not apply form values: {exc}')
			return False

	def propagate_shared_map_desc_entries(self, map_desc_id, map_desc_entries):
		if self.document is None:
			return
		for entry in self.document.get('entries', []):
			fields = entry.get('fields', {}) if isinstance(entry.get('fields'), dict) else {}
			if fields.get('map_desc_id') == map_desc_id:
				entry['map_desc_entries'] = copy.deepcopy(map_desc_entries)

	def update_spawn_icons(self):
		self.primary_icon_image = self.update_single_species_icon(self.primary_icon_label, self.primary_species_var.get())
		self.secondary_icon_image = self.update_single_species_icon(self.secondary_icon_label, self.secondary_species_var.get())
		self.tertiary_icon_image = self.update_single_species_icon(self.tertiary_icon_label, self.tertiary_species_var.get())

	def update_single_species_icon(self, label_widget, selected_label):
		symbol = self.label_to_species_symbol(selected_label)
		species_id = self.species_symbol_to_id(symbol)
		icon_path = self.find_species_icon_path(species_id)
		image = self.get_cached_icon(icon_path, SPECIES_ICON_PX)
		if image is not None:
			label_widget.configure(image=image, text='')
		else:
			label_widget.configure(image='', text=os.path.basename(icon_path))
		return image

	def species_symbol_to_id(self, symbol):
		member = getattr(self.enumlib.SpeciesEnum, symbol, None)
		if member is None:
			return 0
		return int(member.value)

	def find_species_icon_path(self, species_id):
		candidates = [
			os.path.join(ICON_DIR, f'{species_id}{MAIN_ICON_SUFFIX}.png'),
			os.path.join(ICON_DIR, f'{species_id}.png'),
			os.path.join(ICON_DIR, FALLBACK_ICON_NAME),
		]
		for candidate in candidates:
			if os.path.isfile(candidate):
				return candidate
		return candidates[-1]

	def get_cached_icon(self, path, max_px):
		cache_key = (path, max_px)
		if cache_key in self.image_cache:
			return self.image_cache[cache_key]
		image = self.load_icon_image(path, max_px)
		self.image_cache[cache_key] = image
		return image

	def load_icon_image(self, path, max_px):
		if not os.path.isfile(path):
			return None
		try:
			image = tk.PhotoImage(file=path)
			width = image.width()
			height = image.height()
			if width > max_px or height > max_px:
				scale = max(1, math.ceil(max(width / max_px, height / max_px)))
				image = image.subsample(scale, scale)
			return image
		except Exception:
			return None

	def save_json(self):
		if self.document is None:
			return
		if not self.save_current_row_to_document(silent=False):
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
		if not self.save_current_row_to_document(silent=False):
			return
		path = filedialog.asksaveasfilename(
			title='Save StageDescData JSON',
			defaultextension='.json',
			filetypes=[('JSON files', '*.json'), ('All files', '*.*')],
			initialfile=os.path.basename(self.json_path) if self.json_path else 'StageDescData.json',
		)
		if not path:
			return
		self.json_path = path
		self.file_label_var.set(path)
		self.save_json()

	def rebuild_pkc(self):
		if self.document is None:
			return
		if not self.save_current_row_to_document(silent=False):
			return

		source_pkc = filedialog.askopenfilename(
			title='Select original StageDescData PKC',
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
			updated_document = self.backend.apply_json_to_document(pkc_document, self.document)
			pkc_bytes = self.backend.encode_pkc_document(updated_document)
			with open(output_pkc, 'wb') as outfile:
				outfile.write(pkc_bytes)
			self.status_var.set(f'Rebuilt PKC written to {output_pkc}')
			messagebox.showinfo('Rebuild complete', f'Wrote {output_pkc}')
		except Exception as exc:
			messagebox.showerror('Rebuild failed', str(exc))
			self.status_var.set(f'Rebuild failed: {exc}')

	def open_randomizer_options(self):
		if self.document is None:
			return
		if self._randomizer_window is not None and self._randomizer_window.winfo_exists():
			self._randomizer_window.deiconify()
			self._randomizer_window.lift()
			self._randomizer_window.focus_force()
			return

		window = tk.Toplevel(self.root)
		window.title('Randomizer Options')
		window.transient(self.root)
		window.grab_set()
		self._randomizer_window = window

		difficulty_vars = {}
		rank_vars = {}
		level_vars = {}

		outer = ttk.Frame(window, padding=10)
		outer.pack(fill='both', expand=True)

		ttk.Label(outer, text='Select one or more difficulties, ranks and levels to randomize.', font=('TkDefaultFont', 10, 'bold')).pack(anchor='w', pady=(0, 10))

		lists = ttk.Frame(outer)
		lists.pack(fill='both', expand=True)

		difficulty_frame = ttk.LabelFrame(lists, text='Difficulties', padding=8)
		difficulty_frame.grid(row=0, column=0, sticky='nsew', padx=(0, 8))
		rank_frame = ttk.LabelFrame(lists, text='Ranks', padding=8)
		rank_frame.grid(row=0, column=1, sticky='nsew', padx=(0, 8))
		level_frame = ttk.LabelFrame(lists, text='Levels', padding=8)
		level_frame.grid(row=0, column=2, sticky='nsew')

		for column in range(3):
			lists.grid_columnconfigure(column, weight=1)

		for row_index, member in enumerate(self.difficulty_members):
			label = self.difficulty_symbol_to_label[member.name]
			variable = tk.BooleanVar(value=True)
			difficulty_vars[member.name] = variable
			ttk.Checkbutton(difficulty_frame, text=label, variable=variable).grid(row=row_index, column=0, sticky='w')

		for row_index, member in enumerate(self.rank_members):
			label = self.rank_symbol_to_label[member.name]
			variable = tk.BooleanVar(value=True)
			rank_vars[member.name] = variable
			ttk.Checkbutton(rank_frame, text=label, variable=variable).grid(row=row_index, column=0, sticky='w')

		for row_index, member in enumerate(self.stage_members):
			label = self.stage_symbol_to_label[member.name]
			variable = tk.BooleanVar(value=True)
			level_vars[member.name] = variable
			ttk.Checkbutton(level_frame, text=label, variable=variable).grid(row=row_index, column=0, sticky='w')

		button_row = ttk.Frame(outer)
		button_row.pack(fill='x', pady=(10, 0))

		def close_window():
			if self._randomizer_window is not None and self._randomizer_window.winfo_exists():
				self._randomizer_window.destroy()
			self._randomizer_window = None

		def run_randomizer():
			selected_difficulties = {symbol for symbol, variable in difficulty_vars.items() if variable.get()}
			selected_ranks = {symbol for symbol, variable in rank_vars.items() if variable.get()}
			selected_levels = {symbol for symbol, variable in level_vars.items() if variable.get()}
			if not selected_difficulties or not selected_ranks or not selected_levels:
				messagebox.showerror('Invalid selection', 'Select at least one difficulty, one rank and one level.', parent=window)
				return
			self.run_randomizer(selected_difficulties, selected_ranks, selected_levels)
			close_window()

		ttk.Button(button_row, text='Randomize', command=run_randomizer).pack(side='left')
		ttk.Button(button_row, text='Close', command=close_window).pack(side='left', padx=(8, 0))
		window.protocol('WM_DELETE_WINDOW', close_window)

	def run_randomizer(self, selected_difficulties, selected_ranks, selected_levels):
		if self.document is None:
			return
		if not self.save_current_row_to_document(silent=False):
			return

		all_species_symbols = [member.name for member in self.species_members]
		changed_rows = 0
		changed_entries = 0
		shared_updates = {}

		for entry in self.document.get('entries', []):
			if not self.entry_matches_randomizer_filters(entry, selected_difficulties, selected_ranks, selected_levels):
				continue
			map_desc_entries = entry.get('map_desc_entries', [])
			entry_changed = False
			for map_entry in map_desc_entries:
				for row in map_entry.get('enemy_rows', []):
					primary, secondary, tertiary = self.random_species_triplet(all_species_symbols)
					row['species_primary'] = primary
					row['species_secondary'] = None if secondary == 'SPECIES_NONE' else secondary
					row['species_tertiary'] = None if tertiary == 'SPECIES_NONE' else tertiary
					changed_rows += 1
					entry_changed = True
			if entry_changed:
				changed_entries += 1
				fields = entry.get('fields', {}) if isinstance(entry.get('fields'), dict) else {}
				map_desc_id = fields.get('map_desc_id')
				shared_updates[map_desc_id] = copy.deepcopy(map_desc_entries)

		for map_desc_id, map_desc_entries in shared_updates.items():
			self.propagate_shared_map_desc_entries(map_desc_id, map_desc_entries)

		self.rebuild_selection_options()
		self.status_var.set(f'Randomized {changed_rows} spawn lists across {changed_entries} matching stage groups.')
		messagebox.showinfo('Randomizer complete', f'Randomized {changed_rows} spawn lists across {changed_entries} matching stage groups.')

	def entry_matches_randomizer_filters(self, entry, selected_difficulties, selected_ranks, selected_levels):
		for target in entry.get('targets', []):
			if not isinstance(target, dict):
				continue
			if target.get('difficulty') in selected_difficulties and target.get('rank') in selected_ranks and target.get('stage_type') in selected_levels:
				return True
		return False

	def random_species_triplet(self, all_species_symbols):
		while True:
			primary = random.choice(all_species_symbols)
			secondary = random.choice(all_species_symbols)
			tertiary = random.choice(all_species_symbols)
			if not (primary == 'SPECIES_NONE' and secondary == 'SPECIES_NONE' and tertiary == 'SPECIES_NONE'):
				return primary, secondary, tertiary

	def sortable_number(self, value):
		try:
			return float(value)
		except Exception:
			return float('inf')

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



def main():
	root = tk.Tk()
	app = StageDescDataJSONGUI(root)
	if len(sys.argv) > 1:
		path = sys.argv[1]
		if os.path.isfile(path):
			app.load_json_file(path)
	root.mainloop()


if __name__ == '__main__':
	main()
