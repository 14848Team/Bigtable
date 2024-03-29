#This file builds the tablet server.

from flask import Flask, request
import argparse
import os
import os.path as osp
import table_api
import json
import requests
import global_v as Global
from op_api import MemTable

# import logging
#
# log = logging.getLogger('werkzeug')
# log.setLevel(logging.ERROR)


global metadata
global memtable
global memindex
global ssindex_path
global wal_path

app = Flask(__name__)


@app.route('/')
def hello_world():
    return 'hello world'


@app.route('/api/tables', methods=['GET'])
def get_list_tables():
    global metadata
    res = {'tables': list(metadata.keys())}
    return res, 200

#Get Table Info | API: 'GET' /api/tables/<Table_name> 
@app.route('/api/tables/<Table_name>', methods=['GET'])
def get_table_info(Table_name):
    global metadata
    if Table_name in metadata:
        table_info = metadata[Table_name]
        res = {key: table_info[key] for key in metadata[Table_name] if key not in ['filenames', 'row_num', 'row_keys']}
        return res, 200
    else:
    # 404 if the table is not existed
        return "", 404 

#Destroy Table | API: 'DELETE' /api/tables/<Table_name> 
@app.route('/api/tables/<Table_name>', methods=['DELETE'])
def table_delete(Table_name):
    global metadata
    global memtable
    global ssindex_path
    global wal_path
    global memindex
    try: 
        table_api.delete_table(Table_name, metadata, memindex, memtable, ssindex_path, wal_path)
    except NameError: 
    # 404 if the table is not existed
        return "", 404
    return "", 200


@app.route('/api/tables', methods=['POST'])
def post_create_table():
    table_schema = request.get_json(force=True, silent=True)
    if table_schema is None:
        return "", 400
    global metadata
    try:
        table_api.create_table(table_schema, metadata)
    except NameError:
        return "", 409
    return "", 200


@app.route('/api/table/<table_name>/cell', methods=['POST'])
def post_insert_cell(table_name):
    global metadata
    global memtable
    global ssindex_path
    global wal_path
    global memindex
    # parse json input data
    payload = request.get_json(force=True, silent=True)
    # table name not exist
    if table_name not in metadata:
        return '', 404
    column_family_key = payload['column_family']
    table_info = metadata[table_name]
    column_family_info = [column_family_info for column_family_info in
                          table_info['column_families'] if column_family_info['column_family_key'] == column_family_key]
    # column family not exist
    if not len(column_family_info):
        return '', 400
    assert len(column_family_info) == 1
    column_family_info = column_family_info[0]
    column_key = payload['column']
    # column not exist
    if column_key not in column_family_info['columns']:
        return '', 400
    # can insert a cell data
    memtable.insert(table_name, payload, memindex, metadata, ssindex_path, wal_path)
    return '', 200


@app.route('/api/table/<table_name>/cell', methods=['GET'])
def get_retrieve_cell(table_name):
    global metadata
    global memtable
    global memindex
    payload = request.get_json(force=True, silent=True)
    # table name not exist
    if table_name not in metadata:
        return '', 404
    column_family_key = payload['column_family']
    table_info = metadata[table_name]
    column_family_info = [column_family_info for column_family_info in
                          table_info['column_families'] if column_family_info['column_family_key'] == column_family_key]
    # column family not exist
    if not len(column_family_info):
        return '', 400
    assert len(column_family_info) == 1
    column_family_info = column_family_info[0]
    column_key = payload['column']
    # column not exist
    if column_key not in column_family_info['columns']:
        return '', 400
    # can retrieve cell data
    res = memtable.retrieve(table_name=table_name, payload=payload, mem_index=memindex)
    return res, 200


@app.route('/api/table/<table_name>/row', methods=['GET'])
def get_retrieve_row(table_name):
    global metadata
    global memtable
    global memindex
    payload = request.get_json(force=True, silent=True)
    # table name not exist
    if table_name not in metadata:
        return '', 404

    # retrieve from memtable and sstable
    retrieve_row_res = memtable.retrieve_row(table_name=table_name, payload=payload, metadata=metadata,
                                             mem_index=memindex)
    return retrieve_row_res, 200


@app.route('/api/table/<table_name>/cells', methods=['GET'])
def get_retrieve_cells(table_name):
    global metadata
    global memtable
    global memindex
    payload = request.get_json(force=True, silent=True)
    # table name not exist
    if table_name not in metadata:
        return '', 404
    column_family_key = payload['column_family']
    table_info = metadata[table_name]
    column_family_info = [column_family_info for column_family_info in
                          table_info['column_families'] if column_family_info['column_family_key'] == column_family_key]
    # column family not exist
    if not len(column_family_info):
        return '', 400
    assert len(column_family_info) == 1
    column_family_info = column_family_info[0]
    column_key = payload['column']
    # column not exist
    if column_key not in column_family_info['columns']:
        return '', 400
    # row_from greater than row_to
    row_from_key = payload['row_from']
    row_to_key = payload['row_to']
    if row_from_key > row_to_key:
        return '', 400
    # retrieve from memtable and sstable
    res = memtable.retrieve_cells(table_name=table_name, payload=payload, mem_index=memindex, metadata=metadata)
    if res is None:
        return '', 400
    return res, 200


# just for test and debug
@app.route('/api/memtable', methods=['GET'])
def get_memtable():
    global memtable
    return json.dumps(memtable.table, indent=2), 200


@app.route('/api/memtable', methods=['POST'])
def set_memtable():
    global metadata
    global memtable
    global memindex
    global ssindex_path
    global wal_path
    payload = request.get_json(force=True, silent=True)
    if len(payload) == 1 and "memtable_max" in payload and payload["memtable_max"] > 0:
        memtable.set_max_entries(payload, memindex, ssindex_path, wal_path, metadata)
        return "", 200
    else:
        return "", 400

#Sharding | API: 'POST' /api/sharding/<table_name> 
#Input: {"index": the data of index, "type": the type of the row key}
@app.route('/api/sharding/<table_name>', methods=['POST'])
def post_sharding(table_name):
    global memindex
    global ssindex_path
    global metadata
    data = request.get_json(force=True, silent=True)
    index = {}
    for row in data["index"]:
        if data["types"][row] == "int":
            index[int(row)] = data["index"][row]
        else:
            index[row] = data["index"][row]
    #Merge the memindex
    for row in index:
        if row in memindex:
            memindex[row][table_name] = index[row][table_name]
        else:
            memindex[row] = index[row]
    #Create sstable file
    table_filename = table_name + "_1" + '.json'
    with open(osp.join(Global.get_sstable_folder(), table_filename), 'w+') as fp:
        fp.write('[]')
    #Change metadata
    metadata[table_name] = {"name": table_name, "column_families": data["column_families"], "row_num": [0],
                            "row_keys": data['row_keys'], "filenames": [table_filename]}
    #Write metadata
    with open(Global.get_metadata_path(), 'w') as fp:
        json.dump(metadata, fp)
    #Write ssindex
    with open(ssindex_path, 'w') as fp:
        json.dump(memindex, fp)
    return "", 200

#Check whether the server is connected | API: 'GET' /api/connect
@app.route('/api/connect', methods=['GET'])
def connect_tablet():
    #return 200 if the server is connected
    return "", 200

#Recovery | API: 'POST' /api/recovery
#Input {'ssindex': path, 'metadata': path, 'wal': path} (The data of the unconnected server)
@app.route('/api/recovery', methods=['POST'])
def tablet_recovery():
    global memtable
    global memindex
    global metadata
    
    data = request.get_json(force=True, silent=True)
    #Open the ssindex file and merge the data
    with open(data["ssindex"], 'r') as f:
        recovery_ssindex = json.load(f)
    for row in recovery_ssindex:
        if row in memindex:
            for table in recovery_ssindex[row]:
                memindex[row][table] = recovery_ssindex[row][table]
        else:
            memindex[row] = recovery_ssindex[row]
    #Open the metadata file and merge the data
    with open(data["metadata"], 'r') as f:
        recovery_metadata = json.load(f)
    for table in recovery_metadata:
    #If the table is not existed
        if table not in metadata:
            metadata[table] = recovery_metadata[table]
            metadata[table]["filenames"] = [table + "_1.json"]
            metadata[table]["row_num"] = [0]
            filepath = osp.join(Global.get_sstable_folder(), table + "_1.json")
            with open(filepath, 'w+') as fp:
                fp.write('{}')
    #If the table is existed
        else:
            metadata[table]["row_keys"] = metadata[table]["row_keys"] + recovery_metadata[table]["row_keys"]
    #Open the wal file and recovery
    with open(data["wal"], 'r') as f:
        for line in f:
            walline = json.loads(line)
            table_name = walline["table_name"]
            walline.pop("table_name")
            memtable.insert(table_name, walline, memindex, metadata, ssindex_path, wal_path, recover=True,
                            tablet_recover=True)
    return '', 200

# Get arguments
def get_args_parser():
    parser = argparse.ArgumentParser()
    parser.add_argument('tablet_hostname', type=str, help='tablet hostname address')
    parser.add_argument('tablet_port', type=int, help='tablet port number')
    parser.add_argument('master_hostname', type=str, help='master hostname address')
    parser.add_argument('master_port', type=int, help='master port number')
    parser.add_argument('wal', type=str, help='path to Write Ahead Log (WAL) file')
    parser.add_argument('sstable_folder', type=str, help='path to SSTable folder')
    return parser

#Build the url
def com_url(hostname, port, path):
    portstr = str(port)
    url = f"http://{hostname}:{portstr}{path}"
    return url


def main():
    global ssindex_path
    global wal_path
    global memtable
    global metadata
    global memindex

    parser = get_args_parser()
    args = parser.parse_args()

    #Arguments
    wal_path = args.wal
    master_hostname = args.master_hostname
    master_port = args.master_port
    tablet_hostname = args.tablet_hostname
    tablet_port = args.tablet_port
    sstable_folder = args.sstable_folder
    metadata_path = osp.join(sstable_folder, 'metadata.json')
    ssindex_path = osp.join(osp.split(wal_path)[0], 'ssindex.json')

    #Set global arguments
    Global.set_wal_path(wal_path)
    Global.set_sstable_folder(sstable_folder)
    Global.set_metadata_path(metadata_path)
    Global.set_master_hostname(master_hostname)
    Global.set_master_port(master_port)
    Global.set_tablet_hostname(tablet_hostname)
    Global.set_tablet_port(tablet_port)

    #Create wal file
    if not osp.exists(wal_path):
        os.mknod(wal_path)

    #Create sstable folder
    if not osp.exists(sstable_folder):
        os.makedirs(sstable_folder)

    #Create metadata
    if not osp.exists(metadata_path):
        with open(metadata_path, 'w+') as fp:
            fp.write('{}')

    #Create ssindex 
    if not osp.exists(ssindex_path):
        with open(ssindex_path, 'w+') as fp:
            fp.write('{}')

    #Load metadata
    with open(metadata_path, 'r') as fp:
        metadata = json.load(fp)

    memtable = MemTable()

    #Load ssindex into memindex
    with open(ssindex_path, 'r') as f:
        memindex = json.load(f)

    #Recovery if the wal is not empty
    if osp.getsize(wal_path):
        with open(wal_path, 'r') as f:
            for line in f:
                walline = json.loads(line)
                table_name = walline["table_name"]
                walline.pop("table_name")
                memtable.insert(table_name, walline, memindex, metadata, ssindex_path, wal_path, recover=True)

    #Tell the master server
    url = com_url(master_hostname, master_port, '/api/tablet')
    send_wal = osp.abspath(wal_path)
    send_ssindex = osp.abspath(ssindex_path)
    send_metadata = osp.abspath(metadata_path)
    host_port = {"host": tablet_hostname, "port": tablet_port, "wal": send_wal, "ssindex": send_ssindex,
                 "metadata": send_metadata}
    requests.post(url, json=host_port)

    app.run(args.tablet_hostname, args.tablet_port)


if __name__ == '__main__':
    main()
