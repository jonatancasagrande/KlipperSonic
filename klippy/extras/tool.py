import json
import logging
import os
import re, threading
from subprocess import call
import time
import random

def compress_key701(code, data):
    if code == "key701":
        try:
            data = data.get("jobs", [])[0] if data.get("jobs", []) else {}
            metadata = data.get("metadata", {})
            model_info = metadata.get("model_info", {})
            result = "%s|%s|%s|%s|%s|%s|%s|%s|%s|%s|%s|%s|%s|%s|%s|%s|%s|%s|%s|%s|%s|%s|%s|%s|%s|%s|%s|%s|%s|%s|%s" % (
                data.get("end_time", 0), data.get("filament_used", 0), data.get("filename", ""), data.get("print_duration", 0),
                data.get("start_time", 0), data.get("status", ""), data.get("error_msg", ""), data.get("total_duration", 0), 
                metadata.get("estimated_time", 0), metadata.get("filament_total", 0), metadata.get("filament_weight_total", 0), metadata.get("first_layer_bed_temp", 0), metadata.get("first_layer_extr_temp", 0),
                metadata.get("first_layer_height", 0), metadata.get("gcode_end_byte", 0), metadata.get("gcode_start_byte", 0), metadata.get("layer_count", 0),
                metadata.get("layer_height", 0), metadata.get("modified", 0), metadata.get("object_height", 0), metadata.get("size", 0),
                metadata.get("slicer", ""), metadata.get("slicer_version", ""), model_info.get("MaterialType", ""), model_info.get("MaterialName", ""),
                model_info.get("MAXX", 0), model_info.get("MAXY", 0), model_info.get("MAXZ", 0), model_info.get("MINX", 0), model_info.get("MINY", 0), model_info.get("MINZ", 0),
            )
            # "end_time|filament_used|filename|print_duration|start_time|status|error_msg|total_duration|estimated_time|filament_total|filament_weight_total|first_layer_bed_temp|first_layer_extr_temp|first_layer_height|gcode_end_byte|gcode_start_byte|layer_count|layer_height|modified|object_height|size|slicer|slicer_version|MaterialType|MaterialName|MAXX|MAXY|MAXZ|MINX|MINY|MINZ"
            return result
        except Exception as err:
            logging.exception(err)
    return None


def get_print_file_metadata(msg, file_path):
    import re, os, json
    from subprocess import check_output
    # 获取文件名和目录名  
    dir_name, file_name = os.path.split(file_path)
    result = {"file": file_name, "metadata": {}}
    python_env = "/usr/share/klippy-env/bin/python3"
    cmd = "%s /usr/share/klipper/klippy/extras/metadata.py -f '%s' -p %s" % (python_env, file_name, dir_name)
    try:
        result = json.loads(check_output(cmd, shell=True).decode("utf-8"))
    except Exception as err:
        logging.error(err)
    count = 3000
    try:
        with open(file_path, "r") as f:
            while count:
                count -= 1
                line = f.readline() 
                if not line.startswith(";"):
                    continue
                if re.findall(r";MINX:(.*)\n", line):  
                    result["metadata"]["MINX"] = float(re.findall(r";MINX:(.*)\n", line)[0].strip())
                if re.findall(r";MINY:(.*)\n", line):  
                    result["metadata"]["MINY"] = float(re.findall(r";MINY:(.*)\n", line)[0].strip()) 
                if re.findall(r";MINZ:(.*)\n", line):  
                    result["metadata"]["MINZ"] = float(re.findall(r";MINZ:(.*)\n", line)[0].strip())
                if re.findall(r";MAXX:(.*)\n", line):  
                    result["metadata"]["MAXX"] = float(re.findall(r";MAXX:(.*)\n", line)[0].strip()) 
                if re.findall(r";MAXY:(.*)\n", line):  
                    result["metadata"]["MAXY"] = float(re.findall(r";MAXY:(.*)\n", line)[0].strip())
                if re.findall(r";MAXZ:(.*)\n", line):  
                    result["metadata"]["MAXZ"] = float(re.findall(r";MAXZ:(.*)\n", line)[0].strip())
                if re.findall(r";Machine Height:(.*)\n", line):  
                    result["metadata"]["MachineHeight"] = float(re.findall(r";Machine Height:(.*)\n", line)[0].strip())
                if re.findall(r";Machine Width:(.*)\n", line):  
                    result["metadata"]["MachineWidth"] = float(re.findall(r";Machine Width:(.*)\n", line)[0].strip())
                if re.findall(r";Machine Depth:(.*)\n", line):  
                    result["metadata"]["MachineDepth"] = float(re.findall(r";Machine Depth:(.*)\n", line)[0].strip())
                if re.findall(r";Material name:(.*)\n", line):  
                    result["metadata"]["MaterialName"] = str(re.findall(r";Material name:(.*)\n", line)[0].strip())
    except Exception as err:
        logging.error(err)
    send(msg, result)
    return result


def send(msg, data={}):
    pipeFilePath = "/mnt/UDISK/pipe"
    try:
        if not os.path.exists(pipeFilePath):
            call("touch %s" % pipeFilePath, shell=True)
            os.chmod(pipeFilePath, 0o700)
        net_state = call("ping -c 2 -w 2 api.crealitycloud.com > /dev/null 2>&1", shell=True)
        if net_state:
            return
        ret = re.findall('key.*?,', msg)
        if ret:
            msg = ret[0].strip('"').strip(",").replace('"', '')
            if os.path.getsize(pipeFilePath) > 0:
                random_float = random.uniform(0.1, 1)
                time.sleep(random_float)
            result = compress_key701(msg, data)
            if result:
                data = result
            if os.path.getsize(pipeFilePath) == 0:
                send_data = {"reqId": str(int(time.time()*1000)), "dn": "00000000000000", "code": msg, "data": data}
                with open(pipeFilePath, "w") as f:
                    f.write(json.dumps(send_data))
                    f.flush()
    except Exception as err:
        logging.error("reportInformation err:%s" % err)

def reportInformation(msg, data={}):
    t = threading.Thread(target=send, args=(msg, data))
    t.start()

def reportPrintFileInfo(msg, file_path):
    t = threading.Thread(target=get_print_file_metadata, args=(msg, file_path))
    t.start()
