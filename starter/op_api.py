import copy
import json
import requests
import os.path as osp

import global_v as Global


class MemTable:

    def __init__(self):
        self.max_entries = 100
        self.table = []
        self.cell_data_max_num = 5

    def insert(self, table_name, payload, mem_index, metadata, ssindex_path, wal_path, recover=False,
               tablet_recover=False):
        column_family_key, column_key, row_key, cell_data = payload['column_family'], \
                                                            payload['column'], \
                                                            payload['row'], \
                                                            payload['data']
        # find index to insert a cell
        row_insert_index = mem_find_row_index(table=self.table, row_key=row_key, table_name=table_name)
        if row_insert_index == len(self.table) or self.table[row_insert_index]['row'] != row_key or \
                self.table[row_insert_index]['table_name'] != table_name:
            # size of memtable has reached to the maximum value
            if len(self.table) == self.max_entries:
                self.spill(start=0, mem_index=mem_index, ssindex_path=ssindex_path, wal_path=wal_path,
                           metadata=metadata)
                row_insert_index = mem_find_row_index(table=self.table, row_key=row_key, table_name=table_name)
            # cannot find a specific row, insert a new one
            new_row = {
                'row': row_key,
                'table_name': table_name,
                'column_families': {}
            }
            # construct empty column families and columns container
            for column_family_info in metadata[table_name]['column_families']:
                new_row['column_families'][column_family_info['column_family_key']] = {}
                for metadata_column_key in column_family_info['columns']:
                    new_row['column_families'][column_family_info['column_family_key']][metadata_column_key] = []
            # insert new row to table
            self.table.insert(row_insert_index, new_row)

        if not recover:
            # write change into wal
            with open(wal_path, 'a') as fp:
                new_wal = {'table_name': table_name}
                new_wal.update(payload)
                new_wal_line = '{}\n'.format(json.dumps(new_wal))
                fp.write(new_wal_line)

        # inject cell data
        cell_data_l = self.table[row_insert_index]['column_families'][column_family_key][column_key]
        if len(cell_data_l) == self.cell_data_max_num:
            cell_data_l.pop(0)
        cell_data_l += cell_data

        if row_key not in metadata[table_name]['row_keys']:
            metadata[table_name]['row_keys'].append(row_key)
            with open(Global.get_metadata_path(), 'w') as fp:
                json.dump(metadata, fp)

        if len(metadata[table_name]['row_keys']) == 1000 and (not tablet_recover):
            self.spill(start=0, mem_index=mem_index, ssindex_path=ssindex_path, wal_path=wal_path, metadata=metadata)
            keys_before_sharding = metadata[table_name]['row_keys']
            keys_before_sharding.sort()

            keys_left = keys_before_sharding[0:500]
            keys_go = keys_before_sharding[500:]

            mem_index_sent = {}

            type_dict = {}

            for key_go in keys_go:
                type_dict[key_go] = type(key_go).__name__
                if table_name in mem_index[key_go]:
                    if key_go not in mem_index_sent:
                        mem_index_sent[key_go] = {}
                    mem_index_sent[key_go][table_name] = mem_index[key_go][table_name]
                    mem_index[key_go].pop(table_name)
                    if len(mem_index[key_go]) == 0:
                        mem_index.pop(key_go)

            schema_sent = metadata[table_name]['column_families']

            res = {'index': mem_index_sent, 'column_families': schema_sent, 'row_keys': keys_go, 'types': type_dict}

            post_url = 'http://{}:{}/api/sharding/{}/{}/{}/{}'.format(Global.get_master_hostname(),
                                                                      Global.get_master_port(),
                                                                      Global.get_tablet_hostname(),
                                                                      Global.get_tablet_port(), table_name, keys_go[0])
            requests.post(url=post_url, json=res)

            metadata[table_name]['row_keys'] = keys_left
            with open(Global.get_metadata_path(), 'w') as fp:
                json.dump(metadata, fp)

    def retrieve(self, table_name, payload, mem_index):
        row = payload["row"]
        column_family = payload["column_family"]
        column = payload["column"]

        mem_find = mem_find_row_index(self.table, row, table_name)
        mem_data = []
        sstable_data = []
        if mem_find < len(self.table) and self.table[mem_find]["row"] == row and self.table[mem_find][
            "table_name"] == table_name:
            mem_data = self.table[mem_find]["column_families"][column_family][column]
        if row in mem_index:
            if table_name in mem_index[row]:
                with open(mem_index[row][table_name]['filename'], 'r') as f:
                    sstable = json.load(f)
                sstable_data = sstable[mem_index[row][table_name]['offset']]["column_families"][column_family][column]
        retrieve_data = sstable_data + mem_data
        if len(retrieve_data) > 5:
            del retrieve_data[0: len(retrieve_data) - 5]
        return {"row": row, "data": retrieve_data}

    def retrieve_row(self, table_name, payload, metadata, mem_index):
        row_key = payload['row']

        # retrieve result in memtable
        memtable_res = [row_res for row_res in self.table if
                        row_res['table_name'] == table_name and row_res['row'] == row_key]
        memtable_res = memtable_res[0] if len(memtable_res) else {}
        sstable_res = {}
        # retrieve result  in sstable in disk
        # find if the row_key is in the specific sstable file
        if row_key in mem_index and table_name in mem_index[row_key]:
            sstable_path = mem_index[row_key][table_name]['filename']
            with open(sstable_path, 'r') as fp:
                sstable = json.load(fp)
            sstable_res = sstable[mem_index[row_key][table_name]['offset']]

        all_res = {
            "row": row_key,
            "column_families": []
        }

        metadata_column_families = metadata[table_name]['column_families']
        for metadata_column_family in metadata_column_families:
            all_res_column_family = {metadata_column_family['column_family_key']: {'columns': []}}
            for metadata_column in metadata_column_family['columns']:
                all_res_column = {metadata_column: {'data': []}}
                all_res_column_family[metadata_column_family['column_family_key']]['columns'].append(all_res_column)
            all_res['column_families'].append(all_res_column_family)

        for all_res_column_family_dict in all_res['column_families']:
            for all_res_column_family_key, all_res_columns_dict in all_res_column_family_dict.items():
                all_res_columns = all_res_columns_dict['columns']
                for all_res_column_dict in all_res_columns:
                    for all_res_column_key, all_res_data_dict in all_res_column_dict.items():
                        all_res_data = all_res_data_dict['data']
                        if sstable_res != {}:
                            for sstable_data in sstable_res['column_families'][all_res_column_family_key][
                                all_res_column_key]:
                                if len(all_res_data) == self.cell_data_max_num:
                                    all_res_data.pop(0)
                                all_res_data.append(sstable_data)
                        if memtable_res != {}:
                            for memtable_data in memtable_res['column_families'][all_res_column_family_key][
                                all_res_column_key]:
                                if len(all_res_data) == self.cell_data_max_num:
                                    all_res_data.pop(0)
                                all_res_data.append(memtable_data)
        return all_res

    def retrieve_cells(self, table_name, payload, mem_index, metadata):
        column_family_key = payload['column_family']
        column_key = payload['column']
        row_from_key = payload['row_from']
        row_to_key = payload['row_to']

        row_from_index = mem_find_row_index(table=self.table, row_key=row_from_key, table_name=table_name)
        row_to_index = mem_find_row_index(table=self.table, row_key=row_to_key, table_name=table_name)

        row_from_index = min(row_from_index, len(self.table) - 1)
        row_to_index = min(row_to_index, len(self.table) - 1)

        while row_from_index < len(self.table) and self.table[row_from_index]['row'] < row_from_key:
            row_from_index += 1
        while row_to_index >= 0 and self.table[row_to_index]['row'] > row_to_key:
            row_to_index -= 1

        res_row_dict = {}
        res_row_keyset = set()

        for row_index in range(row_from_index, row_to_index + 1, 1):
            row_item = self.table[row_index]
            if row_item['table_name'] == table_name:
                row_key = row_item['row']
                res_row_keyset.add(row_key)
                res_row_dict[row_key] = {
                    'row': row_key,
                    'data': [item for item in row_item['column_families'][column_family_key][column_key]]
                }

        sstable = []
        for subtable_fname in sorted(metadata[table_name]['filenames']):
            with open(osp.join(Global.get_sstable_folder(), subtable_fname), 'r') as fp:
                subtable = json.load(fp)
                sstable += subtable
        sstable.sort(key=lambda row_item: row_item['row'])
        row_from_index = find_row_index(sstable, row_from_key)
        row_to_index = find_row_index(sstable, row_to_key)
        for row_index in range(row_from_index, min(row_to_index + 1, len(sstable))):
            row_item = sstable[row_index]
            row_key = row_item['row']
            if row_from_key <= row_key <= row_to_key:
                res_row_keyset.add(row_key)
                if row_key not in res_row_dict:
                    res_row_dict[row_key] = {
                        'row': row_key,
                        'data': []
                    }
                for one_data in row_item['column_families'][column_family_key][column_key]:
                    if len(res_row_dict[row_key]['data']) == 5:
                        res_row_dict[row_key]['data'].pop(0)
                    res_row_dict[row_key]['data'].append(one_data)

        sorted_row_key = sorted(res_row_keyset)
        res = {
            "rows": []
        }
        for row_key in sorted_row_key:
            res['rows'].append(res_row_dict[row_key])

        return res

    #Spill when the memtable is full
    def spill(self, start, mem_index, ssindex_path, wal_path, metadata):
        c_table = self.table[start:]
        #Classify the row by the table
        row_table = classify(c_table, mem_index)
        #Classify the wal by the table 
        wal_table = wal_classify(c_table)
        for table_name in row_table:
            for subtable_name in row_table[table_name]:
                #If the key has already been in the sstable
                if subtable_name != "Not":
                    if len(row_table[table_name][subtable_name]):
                        subtable_path = osp.join(subtable_name)
                        with open(subtable_path, 'r') as f:
                            subtable = json.load(f)
                        for row in row_table[table_name][subtable_name]:
                            merge_row(subtable, row, mem_index)
                        with open(subtable_path, "w") as f:
                            json.dump(subtable, f)
                #If the key has not already been in the sstable
                else:
                    if len(row_table[table_name]["Not"]):
                        if metadata[table_name]["row_num"][-1] != self.max_entries:
                            subtable_path = osp.join(Global.get_sstable_folder(), metadata[table_name]["filenames"][-1])
                            with open(subtable_path, 'r') as f:
                                subtable = json.load(f)
                        else:
                            #Create a new sstable file
                            last_file = metadata[table_name]["filenames"][-1][
                                        0: len(metadata[table_name]["filenames"][-1]) - 5]
                            last_file = last_file.split('_')
                            filenum = str(int(last_file[-1]) + 1)
                            last_file.pop()
                            filefront = '_'.join(last_file)
                            filename = filefront + "_" + filenum + ".json"
                            subtable_path = osp.join(Global.get_sstable_folder(), filename)
                            metadata[table_name]["filenames"].append(filename)
                            metadata[table_name]["row_num"].append(0)
                            with open(subtable_path, 'w+') as f:
                                f.write('[]')
                            with open(subtable_path, 'r') as f:
                                subtable = json.load(f)

                        for row in row_table[table_name]["Not"]:
                            if row["row"] not in mem_index:
                                mem_index[row["row"]] = {}
                            if table_name not in mem_index[row["row"]]:
                                mem_index[row["row"]][table_name] = {}
                            if metadata[table_name]["row_num"][-1] == self.max_entries:
                                #Updata the offset
                                for i, subtable_row in enumerate(subtable):
                                    mem_index[subtable_row["row"]][table_name]["offset"] = i
                                with open(subtable_path, 'w') as f:
                                    json.dump(subtable, f)
                                last_file = metadata[table_name]["filenames"][-1][
                                            0: len(metadata[table_name]["filenames"][-1]) - 5]
                                last_file = last_file.split('_')
                                filenum = str(int(last_file[-1]) + 1)
                                last_file.pop()
                                filefront = '_'.join(last_file)
                                filename = filefront + "_" + filenum + ".json"
                                subtable_path = osp.join(Global.get_sstable_folder(), filename)
                                #Updata metadata
                                metadata[table_name]["filenames"].append(filename)
                                metadata[table_name]["row_num"].append(0)
                                with open(subtable_path, 'w+') as f:
                                    f.write('[]')
                                with open(subtable_path, 'r') as f:
                                    subtable = json.load(f)
                            add_row(subtable, row)
                            metadata[table_name]["row_num"][-1] += 1
                            #Updata the path of the file which contains the row
                            mem_index[row["row"]][table_name]["filename"] = osp.join(Global.get_sstable_folder(),
                                                                                     metadata[table_name]["filenames"][
                                                                                         -1])
                        for i, subtable_row in enumerate(subtable):
                            mem_index[subtable_row["row"]][table_name]["offset"] = i
                        with open(subtable_path, 'w') as f:
                            json.dump(subtable, f)
        self.table = self.table[0: start]
        #Write the metadata into file
        with open(Global.get_metadata_path(), 'w') as f:
            json.dump(metadata, f)
        #Write the memindex into file 
        with open(ssindex_path, 'w') as f:
            json.dump(mem_index, f)
        #Modify the WAL file, delete the record which has spilled
        if start != 0:
            WALlist = []
            with open(wal_path, 'r') as f:
                for line in f:
                    walline = json.loads(line)
                    if str(walline["row"]) + "_" + walline["table_name"] not in wal_table:
                        WALlist.append(line)
            with open(wal_path, 'w') as f:
                for line in WALlist:
                    f.write(line)
        else:
            with open(wal_path, 'w') as f:
                pass

    #Set the max entries of the memtable
    def set_max_entries(self, payload, mem_index, ssindex_path, wal_path, metadata):
        num = payload['memtable_max']
        if num < self.max_entries:
            self.spill(num, mem_index, ssindex_path, wal_path, metadata)
        self.max_entries = num

#Classify the row in memtable by the table
def classify(c_table, mem_index):
    row_table = {}
    for row in c_table:
        row_key = row["row"]
        table_name = row["table_name"]
        column_family = row["column_families"]
        if table_name not in row_table:
            row_table[table_name] = {"Not": []}
        if row_key in mem_index and table_name in mem_index[row_key]:
            if mem_index[row_key][table_name]["filename"] not in row_table[table_name]:
                row_table[table_name][mem_index[row_key][table_name]["filename"]] = []
            row_table[table_name][mem_index[row_key][table_name]["filename"]].append(row)
        else:
            row_table[table_name]["Not"].append(row)
    return row_table

#Classify the WAL file
def wal_classify(c_table):
    wal_table = []
    for row in c_table:
        row_key = str(row["row"])
        table_name = row["table_name"]
        wal_table.append(row_key + "_" + table_name)
    return wal_table

#Merge the row and garbage collection
def merge_row(subtable, row, mem_index):
    row_key = row["row"]
    table_name = row["table_name"]
    row_index = mem_index[row_key][table_name]["offset"]
    for column_family in subtable[row_index]["column_families"]:
        for column in subtable[row_index]["column_families"][column_family]:
            subtable_list = subtable[row_index]["column_families"][column_family][column]
            subtable_list = subtable_list + row["column_families"][column_family][column]
            #Reserve the last five versions 
            if len(subtable_list) > 5:
                del subtable_list[0: len(subtable_list) - 5]
            subtable[row_index]["column_families"][column_family][column] = subtable_list

#Add a new row to sstable
def add_row(subtable, row):
    row_key = row["row"]
    tmp = copy.copy(row)
    tmp.pop("table_name")
    row_index = find_row_index(subtable, row_key)
    subtable.insert(row_index, tmp)

#Find the index of a row through binary search
def find_row_index(table, row_key):
    left = 0
    right = len(table) - 1
    while left <= right:
        mid = left + (right - left) // 2
        if table[mid]['row'] < row_key:
            left = mid + 1
        else:
            right = mid - 1
    return left

#Find the index of a row in memtable, return the last smaller row if the row is not existed
def mem_find_row_index(table, row_key, table_name):
    left = 0
    right = len(table) - 1
    while left <= right:
        mid = left + (right - left) // 2
        if table[mid]['row'] < row_key:
            left = mid + 1
        else:
            right = mid - 1
    if left == len(table) or table[left]['row'] != row_key:
        return left
    else:
        tmpindex = left
        while tmpindex < len(table) and table[tmpindex]['row'] == row_key and table[tmpindex][
            'table_name'] != table_name:
            tmpindex += 1
        if tmpindex < len(table) and table[tmpindex]['row'] == row_key and table[tmpindex]['table_name'] == table_name:
            return tmpindex
        tmpindex = left
        while tmpindex >= 0 and table[tmpindex]['row'] == row_key and table[tmpindex]['table_name'] != table_name:
            tmpindex -= 1
        if tmpindex >= 0 and table[tmpindex]['row'] == row_key and table[tmpindex]['table_name'] != table_name:
            return tmpindex
        return left
