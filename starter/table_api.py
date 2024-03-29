#This file includes the function of operation of the whole talbe, such as creating and deleting. 

import json
from typing import List
import os
import os.path as osp
import global_v as Global


def create_table(table_schema: str, mem_metadata):
    metadata_path = Global.get_metadata_path()
    sstable_folder = Global.get_sstable_folder()

    table_name = table_schema['name']

    if table_name in mem_metadata.keys():
        raise NameError('Table Already Exists')

    table_filename = '{}_{}.json'.format(table_name, 1)
    with open(osp.join(sstable_folder, table_filename), 'w+') as fp:
        fp.write('[]')

    table_schema['filenames'] = [table_filename]
    table_schema['row_num'] = [0]
    table_schema['row_keys'] = []
    mem_metadata[table_name] = table_schema

    with open(metadata_path, 'w') as fp:
        json.dump(mem_metadata, fp)


def delete_table(Table_name, mem_metadata, memindex, memtable, ssindex_path, wal_path):
    if Table_name in mem_metadata:
        #Get sstable file path
        Table_list = mem_metadata[Table_name]['filenames']
        #Delete sstable file
        for Table in Table_list:
            Table_path = osp.join(Global.get_sstable_folder(), Table)
            os.remove(Table_path)
        #Update the metadata
        mem_metadata.pop(Table_name)
        with open(Global.get_metadata_path(), 'w') as f:
            json.dump(mem_metadata, f)

        new_memindex = {}
        #Update memindex
        for row in memindex:
            if Table_name in memindex[row]:
                memindex[row].pop(Table_name)
                if len(memindex[row]) != 0:
                    new_memindex[row] = memindex[row]
        memindex.clear()
        for key in new_memindex:
            memindex[key] = new_memindex[key]
        #Write the ssindex
        with open(ssindex_path, 'w') as f:
            json.dump(memindex, f)
        wallist = []
        #Update WAL
        with open(wal_path, 'r') as f:
            for line in f:
                walrow = json.loads(line)
                if walrow["table_name"] != Table_name:
                    wallist.append(line)
        with open(wal_path, 'w') as f:
            for line in wallist:
                f.write(line)
        #Update memtable
        new_memtable = [row for row in memtable.table if row["table_name"] != Table_name]
        memtable.table.clear()
        for row in new_memtable:
            memtable.table.append(row)
    else:
        raise NameError("Table does not exist!")
