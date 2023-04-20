#!/usr/bin/env python3
#
# Copyright (C) 2023 Advanced Micro Devices. All rights reserved.
#
# Permission is hereby granted, free of charge, to any person obtaining a copy of
# this software and associated documentation files (the "Software"), to deal in
# the Software without restriction, including without limitation the rights to
# use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of
# the Software, and to permit persons to whom the Software is furnished to do so,
# subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS
# FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR
# COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER
# IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN
# CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
#

import csv
import json
import re
import time
import yaml
from enum import Enum

from amdsmi_helpers import AMDSMIHelpers
import amdsmi_cli_exceptions

class AMDSMILogger():
    def __init__(self, compatibility='amdsmi', format='human_readable',
                    destination='stdout') -> None:
        self.output = {}
        self.multiple_device_output = []
        self.watch_output = []
        self.compatibility = compatibility # amd-smi, gpuv-smi, or rocm-smi
        self.format = format # csv, json, or human_readable
        self.destination = destination # stdout, path to a file (append)
        self.amd_smi_helpers = AMDSMIHelpers()


    class LoggerFormat(Enum):
        """Enum for logger formats"""
        json = 'json'
        csv = 'csv'
        human_readable = 'human_readable'

    class LoggerCompatibility(Enum):
        """Enum for logger compatibility"""
        amdsmi = 'amdsmi'
        rocmsmi = 'rocmsmi'
        gpuvsmi = 'gpuvsmi'


    def is_json_format(self):
        return self.format == self.LoggerFormat.json.value


    def is_csv_format(self):
        return self.format == self.LoggerFormat.csv.value


    def is_human_readable_format(self):
        return self.format == self.LoggerFormat.human_readable.value


    def is_amdsmi_compatibility(self):
        return self.compatibility == self.LoggerCompatibility.amdsmi.value


    def is_rocmsmi_compatibility(self):
        return self.compatibility == self.LoggerCompatibility.rocmsmi.value


    def is_gpuvsmi_compatibility(self):
        return self.compatibility == self.LoggerCompatibility.gpuvsmi.value


    class CsvStdoutBuilder(object):
        def __init__(self):
            self.csv_string = []

        def write(self, row):
            self.csv_string.append(row)

        def __str__(self):
            return ''.join(self.csv_string)


    def _capitalize_keys(self, input_dict):
        output_dict = {}
        for key in input_dict.keys():
            # Capitalize key if it is a string
            if isinstance(key, str):
                cap_key = key.upper()
            else:
                cap_key = key

            if isinstance(input_dict[key], dict):
                output_dict[cap_key] = self._capitalize_keys(input_dict[key])
            elif isinstance(input_dict[key], list):
                cap_key_list = []
                for data in input_dict[key]:
                    if isinstance(data, dict):
                        cap_key_list.append(self._capitalize_keys(data))
                    else:
                        cap_key_list.append(data)
                output_dict[cap_key] = cap_key_list
            else:
                output_dict[cap_key] = input_dict[key]

        return output_dict


    def _convert_json_to_human_readable(self, json_object):
        # First Capitalize all keys in the json object
        capitalized_json = self._capitalize_keys(json_object)
        json_string = json.dumps(capitalized_json, indent=4)
        yaml_data = yaml.safe_load(json_string)
        yaml_output = yaml.dump(yaml_data, sort_keys=False, allow_unicode=True)

        if self.is_gpuvsmi_compatibility():
            # Convert from GPU: 0 to GPU 0:
            yaml_output = re.sub('GPU: ([0-9]+)', 'GPU \\1:', yaml_output)

        # Remove a key line if it is a spacer
        yaml_output = yaml_output.replace("AMDSMI_SPACING_REMOVAL:\n", "")
        yaml_output = yaml_output.replace("'", "") # Remove ''

        clean_yaml_output = ''
        for line in yaml_output.splitlines():
            line = line.split(':')

            # Remove dashes and increase tabbing split key
            line[0] = line[0].replace("-", " ", 1)
            line[0] = line[0].replace("  ", "    ")

            # Join cleaned output
            line = ':'.join(line) + '\n'
            clean_yaml_output += line

        return clean_yaml_output


    def flatten_dict(self, target_dict):
        """This will flatten a dictionary out to a single level of key value stores
            removing key's with dictionaries and wrapping each value to in a list
            ex:
                {
                    'usage': {
                        'gfx_usage': 0,
                        'mem_usage': 0,
                        'mm_usage_list': [22,0,0]
                    }
                }
            to:
                {
                    'gfx_usage': 0,
                    'mem_usage': 0,
                    'mm_usage_list': [22,0,0]}
                }

        Args:
            target_dict (dict): Dictionary to flatten
            parent_key (str):
        """
        # print(target_dict)
        output_dict = {}
        # First flatten out values

        # separetly handle ras and process and firmware

        # If there are multi values, and the values are all dicts
        # Then flatten the sub values with parent key
        for key, value in target_dict.items():
            if isinstance(value, dict):
                # Check number of items in the dict
                if len(value.values()) > 1:
                    value_with_parent_key = {}
                    for parent_key, child_dict in value.items():
                        if isinstance(child_dict, dict):
                            for child_key, value1 in child_dict.items():
                                value_with_parent_key[parent_key + '_' + child_key] = value1
                        else:
                            value_with_parent_key[parent_key] = child_dict
                    value = value_with_parent_key

                if self.is_gpuvsmi_compatibility():
                    if key in ('asic', 'bus', 'pcie', 'vbios','board', 'limit'):
                        value_with_parent_key = {}
                        for child_key, child_value in value.items():
                            value_with_parent_key[key + '_' + child_key] = child_value
                        value = value_with_parent_key

                output_dict.update(self.flatten_dict(value).items())
            else:
                output_dict[key] = value
        return output_dict


    def store_output(self, device_handle, argument, data):
        """ Store the argument and device handle according to the compatibility.
                Each compatibility function will handle the output format and
                populate the output
            params:
                device_handle - device handle object to the target device output
                argument (str) - key to store data
                data (dict | list) - Data store against argument
            return:
                Nothing
        """
        gpu_id = self.amd_smi_helpers.get_gpu_id_from_device_handle(device_handle)
        if self.is_amdsmi_compatibility():
            self._store_output_amdsmi(gpu_id=gpu_id, argument=argument, data=data)
        elif self.is_rocmsmi_compatibility():
            self._store_output_rocmsmi(gpu_id=gpu_id, argument=argument, data=data)
        elif self.is_gpuvsmi_compatibility():
            self._store_output_gpuvsmi(gpu_id=gpu_id, argument=argument, data=data)


    def _store_output_amdsmi(self, gpu_id, argument, data):
        if self.is_json_format() or self.is_human_readable_format():
            self.output['gpu'] = int(gpu_id)
            if argument == 'values' and isinstance(data, dict):
                self.output.update(data)
            else:
                self.output[argument] = data
        elif self.is_csv_format():
            # New way is in gpuvsmi func
            self.output['gpu'] = int(gpu_id)

            if argument == 'values' or isinstance(data, dict):
                flat_dict = self.flatten_dict(data)
                self.output.update(flat_dict)
            else:
                self.output[argument] = data

        else:
            raise amdsmi_cli_exceptions(self, "Invalid output format given, only json, csv, and human_readable supported")


    def _store_output_rocmsmi(self, gpu_id, argument, data):
        if self.is_json_format():
            # put output into self.json_output
            pass
        elif self.is_csv_format():
            # put output into self.csv_output
            pass
        elif self.is_human_readable_format():
            # put output into self.human_readable_output
            pass
        else:
            raise amdsmi_cli_exceptions(self, "Invalid output format given, only json, csv, and human_readable supported")


    def _store_output_gpuvsmi(self, gpu_id, argument, data):
        if self.is_json_format() or self.is_human_readable_format():
            self.output['gpu'] = int(gpu_id)
            self.output[argument] = data
        elif self.is_csv_format():
            self.output['gpu'] = int(gpu_id)

            if argument == 'values' or isinstance(data, dict):
                flat_dict = self.flatten_dict(data)
                self.output.update(flat_dict)
            else:
                self.output[argument] = data

            gpuv_flat_dict = {}
            for key, value in self.output.items():
                gpuv_flat_dict[key] = value

                # Change AMDSMI_STATUS strings to N/A for gpuv compatability
                if isinstance(value, str):
                    if 'AMDSMI_STATUS' in value:
                        gpuv_flat_dict[key] = 'N/A'

                # Change bdf and uuid keys for gpuv compatability
                if isinstance(key, str):
                    if key in ('bdf','uuid'):
                        gpuv_flat_dict['gpu_' + key] = gpuv_flat_dict.pop(key)

            self.output = gpuv_flat_dict

        else:
            raise amdsmi_cli_exceptions(self, "Invalid output format given, only json, csv, and human_readable supported")


    def store_multiple_device_output(self):
        """ Store the current output into the multiple_device_output
                then clear the current output
            params:
                None
            return:
                Nothing
        """
        if not self.output:
            return

        self.multiple_device_output.append(self.output)
        self.output = {}


    def store_watch_output(self, multiple_devices=False):
        """ Add the current output or multiple_devices_output
            params:
                multiple_devices (bool) - True if watching multiple devices
            return:
                Nothing
        """
        values = self.output
        if multiple_devices:
            values = self.multiple_device_output

        self.watch_output.append({'timestamp': int(time.time()),
                                    'values': values})


    def print_output(self, multiple_device_output=False, watch_output=False):
        """ Print current output acording to format and then destination
            params:
                multiple_device_output (bool) - True if printing output from
                    multiple devices
                watch_output (bool) - True if printing watch output
            return:
                Nothing
        """
        if self.is_json_format():
            self._print_json_output(multiple_device_output=multiple_device_output,
                                    watch_output=watch_output)
        elif self.is_csv_format():
            self._print_csv_output(multiple_device_output=multiple_device_output,
                                    watch_output=watch_output)
        elif self.is_human_readable_format():
            self._print_human_readable_output(multiple_device_output=multiple_device_output,
                                                watch_output=watch_output)


    def _print_json_output(self, multiple_device_output=False, watch_output=False):
        if multiple_device_output:
            json_output = self.multiple_device_output
        else:
            json_output = self.output

        if self.destination == 'stdout':
            if watch_output:
                return # We don't need to print to stdout at the end of watch
            else:
                json_std_output = json.dumps(json_output, indent = 4)
                print(json_std_output)
        else: # Write output to file
            if watch_output: # Flush the full JSON output to the file on watch command completion
                with self.destination.open('w') as output_file:
                    json.dump(self.watch_output, output_file, indent=4)
            else:
                with self.destination.open('a') as output_file:
                    json.dump(json_output, output_file, indent=4)


    def _print_csv_output(self, multiple_device_output=False, watch_output=False):
        if watch_output: # Don't print output if it's for watch
            return

        if multiple_device_output:
            stored_csv_output = self.multiple_device_output
        else:
            if not isinstance(self.output, list):
                stored_csv_output = [self.output]

        if self.destination == 'stdout':
            csv_header = stored_csv_output[0].keys()
            csv_stdout_output = self.CsvStdoutBuilder()
            writer = csv.DictWriter(csv_stdout_output, csv_header)
            writer.writeheader()
            writer.writerows(stored_csv_output)

            if self.is_gpuvsmi_compatibility():
                print(str(csv_stdout_output).replace('"',''))
            else:
                print(str(csv_stdout_output))
        else:
            with self.destination.open('a', newline = '') as output_file:
                csv_header = stored_csv_output[0].keys()
                writer = csv.DictWriter(output_file, csv_header)
                writer.writeheader()
                writer.writerows(stored_csv_output)


    def _print_human_readable_output(self, multiple_device_output=False, watch_output=False):
        if watch_output: # Don't print output if it's for watch
            return

        if multiple_device_output:
            human_readable_output = ''
            for output in self.multiple_device_output:
                human_readable_output += (self._convert_json_to_human_readable(output))
        else:
            human_readable_output = self._convert_json_to_human_readable(self.output)

        if self.destination == 'stdout':
            try:
                # printing as unicode may fail if locale is not set properly
                print(human_readable_output)
            except UnicodeEncodeError:
                # print as ascii, ignore incompatible characters
                print(human_readable_output.encode('ascii', 'ignore').decode('ascii'))
        else:
            with self.destination.open('a') as output_file:
                output_file.write(human_readable_output)
