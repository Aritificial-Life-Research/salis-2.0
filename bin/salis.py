#!/usr/bin/env python3

""" SALIS: Viewer/controller for the SALIS simulator.

File: salis.py
Author: Paul Oliver
Email: paul.t.oliver.design@gmail.com

Main handler for the Salis simulator. The Salis class takes care of
initializing, running and shutting down the simulator and other sub-modules. It
also takes care of parsing the command-line arguments and linking to the Salis
library with the help of ctypes.

To execute this script, make sure to have python3 installed and in your path,
as well as the cython package. Also, make sure it has correct execute
permissions (chmod).
"""

import os
import re
import sys
import time
from argparse import ArgumentParser, HelpFormatter
from subprocess import check_call

from ctypes import CDLL, c_bool, c_uint8, c_uint32, c_char_p, POINTER

from modules.common import Common
from modules.handler import Handler
from modules.printer import Printer


__version__ = "2.0"


class Salis:
	def __init__(self):
		""" Salis constructor. Arguments are passed through the command line
		and parsed with the 'argparse' module. Library is loaded with 'CDLL'
		and C headers are parsed to detect function argument and return types.
		"""
		# Before declaring any other privates, let's define the autosave
		# member, get the absolute path and parse CLI arguments.
		self.autosave = "---"
		self.path = self.__get_path()
		self.args = self.__parse_args()

		# Now we can declare all other public and private members.
		self.__log = self.__open_log_file()
		self.__exit = False
		self.save_file_path = self.__get_save_file_path()
		self.lib = self.__parse_lib()
		self.common = Common(self)
		self.printer = Printer(self)
		self.handler = Handler(self)
		self.minimal = self.args.minimal

		if self.args.running:
			self.state = "running"
			self.printer.set_nodelay(True)
		else:
			self.state = "paused"

		# Based on CLI arguments, initialize a new Salis simulation or load
		# existing one from file.
		if self.args.action == "new":
			self.lib.sal_main_init(self.args.order)
		elif self.args.action == "load":
			self.lib.sal_main_load(self.save_file_path.encode("utf-8"))

		# Configure Common module. Pass C callbacks to Salis and load settings
		# for this simulator (if they exist).
		self.common.define_functors()

		try:
			self.common.load_network_config(self.args.file + ".json")
		except FileNotFoundError:
			pass

	def __del__(self):
		""" Salis destructor.
		"""
		# In case an error occurred early during initialization, checks whether
		# Salis has been initialized correctly before attempting to shut it
		# down.
		if hasattr(self, "__lib") and hasattr(self.lib, "sal_main_quit"):
			if self.lib.sal_main_is_init():
				self.lib.sal_main_quit()

		# If simulation ended correctly, 'error.log' should be empty. Delete
		# file it exists and its empty.
		if (
			hasattr(self, "_Salis__log") and
			os.path.isfile(self.__log) and
			os.stat(self.__log).st_size == 0
		):
			os.remove(self.__log)

	def cycle(self):
		""" Perform all cycle operations. These include cycling the actual
		Salis simulator, checking for autosave intervals and cycling the Common
		module.
		"""
		self.common.cycle()
		self.lib.sal_main_cycle()
		self.check_autosave()

	def run(self):
		""" Runs main simulation loop. Curses may be placed on non-blocking
		mode, which allows simulation to run freely while still listening to
		user input.
		"""
		while not self.__exit:
			self.printer.print_page()
			self.handler.process_cmd(self.printer.get_cmd())

			# If in non-blocking mode, re-print data once every 15
			# milliseconds.
			if self.state == "running":
				end = time.time() + 0.015

				while time.time() < end:
					self.cycle()

	def toggle_state(self):
		""" Toggle between 'paused' and 'running' states. On 'running' curses
		gets placed in non-blocking mode.
		"""
		if self.state == "paused":
			self.state = "running"
			self.printer.set_nodelay(True)
		else:
			self.state = "paused"
			self.printer.set_nodelay(False)

	def rename(self, new_name):
		""" Give the simulation a new name.
		"""
		self.args.file = new_name
		self.save_file_path = self.__get_save_file_path()

	def set_autosave(self, interval):
		""" Set the simulation's auto-save interval. When set to zero, auto
		saving is disabled,
		"""
		if not interval:
			self.autosave = "---"
		else:
			self.autosave = interval

	def check_autosave(self):
		""" Save compressed simulation file to './sims/auto/*' whenever the
		autosave interval is reached. We use the following naming convention
		for auto-saved files:

		>>> ./sims/auto/<file-name>.<sim-epoch>.<sim-cycle>.auto.gz
		"""
		if self.autosave != "---":
			if not self.lib.sal_main_get_cycle() % self.autosave:
				auto_path = os.path.join(self.path, "sims/auto", ".".join([
					self.args.file,
					"{:08x}".format(self.lib.sal_main_get_epoch()),
					"{:08x}".format(self.lib.sal_main_get_cycle()),
					"auto"
				]))
				self.lib.sal_main_save(auto_path.encode("utf-8"))
				check_call(["gzip", auto_path])

				# Save to main file as well.
				self.lib.sal_main_save(self.save_file_path.encode("utf-8"))

	def exit(self):
		""" Save network settings and signal we want to exit the simulator.
		"""
		self.common.save_network_config(self.args.file + ".json")
		self.__exit = True


	###############################
	# Private methods
	###############################

	def __get_path(self):
		""" Retrieve the absolute path of this script. We need to do this in
		order to detect the './lib', './sims' and './genomes' subdirectories.
		"""
		return os.path.dirname(__file__)

	def __get_save_file_path(self):
		""" Retrieve the absolute path of the file to which we will save this
		simulation when we exit Salis.
		"""
		return os.path.join(self.path, "sims", self.args.file)

	def __parse_args(self):
		""" Parse command-line arguments with the 'argparse' module. To learn
		more about each command, invoke the simulator in one of the following
		ways:

			(venv) $ python tsalis.py --help
			(venv) $ python tsalis.py new --help
			(venv) $ python tsalis.py load --help

		"""
		# Custom formatter helps keep all help data aligned.
		formatter = lambda prog: HelpFormatter(prog, max_help_position=30)

		# Initialize the main parser with our custom formatter.
		parser = ArgumentParser(
			description=("Viewer/controller for the Salis simulator."),
			formatter_class=formatter
		)
		parser.add_argument(
			"-v", "--version", action="version",
			version="Salis: A-Life Simulator (" + __version__ + ")"
		)
		parser.add_argument(
			"-d", "--debug", action="store_true",
			help="run debug build of Salis library"
		)
		parser.add_argument(
			"-m", "--minimal", action="store_true",
			help="start up Salis in minimal mode"
		)
		parser.add_argument(
			"-r", "--running", action="store_true",
			help="start up Salis in running state"
		)

		# Initialize the 'new/load' action subparsers.
		subparsers = parser.add_subparsers(
			dest="action",
			help=(
				"call 'salis.py new --help' or 'salis.py load --help' for "
				"sublist of commands"
			)
		)
		subparsers.required = True

		# Set up subparser for the create 'new' action.
		new_parser = subparsers.add_parser("new", formatter_class=formatter)
		new_parser.add_argument(
			"-o", "--order", required=True, type=lambda x: int(x, 0),
			metavar="[1-31]", help="create new simulation of given ORDER"
		)
		new_parser.add_argument(
			"-f", "--file", required=True, type=str, metavar="FILE",
			help="name of FILE to save simulation to on exit"
		)
		new_parser.add_argument(
			"-a", "--auto", required=False, type=lambda x: int(x, 0),
			metavar="INT", help="auto-save interval for the new simulation"
		)

		# Set up subparser for the 'load' existing action.
		load_parser = subparsers.add_parser("load", formatter_class=formatter)
		load_parser.add_argument(
			"-f", "--file", required=True, type=str, metavar="FILE",
			help="load previously saved simulation from FILE"
		)
		load_parser.add_argument(
			"-a", "--auto", required=False, type=lambda x: int(x, 0),
			metavar="INT", help="auto-save interval for the loaded simulation"
		)

		# Finally, parse all arguments.
		args = parser.parse_args()

		# Revise that parsed CL arguments are valid.
		if args.action == "new":
			if args.order not in range(1, 32):
				parser.error("Order must be an integer between 1 and 31")
		else:
			savefile = os.path.join(self.path, "sims", args.file)

			# No save-file with given name has been detected.
			if not os.path.isfile(savefile):
				parser.error(
					"Save file provided '{}' does not exist".format(savefile)
				)

		# Set autosave interval, if given.
		#if args.auto:
		if args.auto:
			self.set_autosave(args.auto)

		return args

	def __open_log_file(self):
		""" Create a log file to store errors on. It will get deleted if no
		errors are detected.
		"""
		log_file = os.path.join(self.path, "error.log")
		sys.stderr = open(log_file, "w")
		return log_file

	def __parse_lib(self):
		""" Dynamically parse the Salis library C header files. We do this in
		order to more easily set the correct input/output types of all loaded
		functions. C functions to be parsed must be declared in a '.h' file
		located on the '../include' directory, using the following syntax:

			SALIS_API restype func_name(arg1_type arg1, arg2_type arg2);

		Note to developers: the 'SALIS_API' keyword should *NOT* be used
		anywhere else in the header files (not even in comments)!
		"""
		# Load debug or release versions of Salis into a CDLL object.
		if self.args.debug:
			suff = "-deb"
		else:
			suff = "-rel"

		lib = CDLL(os.path.join(self.path, "lib/libsalis{}.so".format(suff)))
		include_dir = os.path.join(self.path, "../include")
		c_includes = [
			os.path.join(include_dir, f)
			for f in os.listdir(include_dir)
			# Only parse '.h' header files.
			if os.path.isfile(os.path.join(include_dir, f)) and f[-2:] == ".h"
		]
		funcs_to_set = []

		for include in c_includes:
			with open(include, "r") as f:
				text = f.read()

			# Regexp to detect C functions to parse. This is a *very lazy*
			# parser. So, if you want to expand/tweak Salis, be careful when
			# declaring new functions!
			funcs = re.findall(r"SALIS_API([\s\S]+?);", text, re.MULTILINE)

			for func in funcs:
				func = func.replace("\n", "")
				func = func.replace("\t", "")
				func = func.strip()
				restype = func.split()[0]
				name = func.split()[1].split("(")[0]
				args = [
					arg.split()[0]
					for arg in func.split("(")[1].split(")")[0].split(",")
				]
				funcs_to_set.append({
					"name": name,
					"restype": restype,
					"args": args
				})

		# All Salis typedefs must be included here, associated to their CTYPES
		# equivalents.
		type_convert = {
			"void": None,
			"boolean": c_bool,
			"uint8": c_uint8,
			"uint8_p": POINTER(c_uint8),
			"uint32": c_uint32,
			"uint32_p": POINTER(c_uint32),
			"string": c_char_p,
			"Process": None,
			"Sender": Common.SENDER_TYPE,
			"Receiver": Common.RECEIVER_TYPE,
		}

		# Finally, set correct arguments and return types of all Salis
		# functions.
		for func in funcs_to_set:
			func["restype"] = type_convert[func["restype"]]
			func["args"] = [type_convert[arg] for arg in func["args"]]
			getattr(lib, func["name"]).restype = func["restype"]
			getattr(lib, func["name"]).argtype = func["args"]

		return lib


###############################
# Entry point
###############################

if __name__ == "__main__":
	Salis().run()
