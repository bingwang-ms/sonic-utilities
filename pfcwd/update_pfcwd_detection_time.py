
import os
import sys

from natsort import natsorted
from swsssdk import ConfigDBConnector
from sonic_py_common import logger
import subprocess

try:
    from sonic_py_common.device_info import get_sonic_version_info
except ImportError:
    try:
        from sonic_device_util import get_sonic_version_info
    except ImportError:
        from sonic_platform import get_sonic_version_info


SYSLOG_IDENTIFIER = "pfcwd_config_patcher"

log = logger.Logger(SYSLOG_IDENTIFIER)

CONFIG_DB_PFC_WD_TABLE_NAME = 'PFC_WD'
PORT_QOS_MAP =  "PORT_QOS_MAP"
POLL_INTERVAL = "POLL_INTERVAL"


def exec_cmd(cmd, verbose=False):
    p = subprocess.Popen(cmd, shell=True, executable='/bin/bash', stdout=subprocess.PIPE)
    outs, errs = p.communicate()
    msg = outs.decode('utf8')
    if outs and verbose: print('exec_cmd stdout = '+msg)
    if errs: print('exec_cmd stderr = '+errs)

    return (p.returncode, msg)


def config_save():
    CMD = "config save -y"
    ret, _ = exec_cmd(CMD)
    if ret != 0:
        log.log_warning("Failed to execute command: {}".format(CMD))
        return False
    return True


def check_sonic_version(config_db):
    platform_info = config_db.get_entry('DEVICE_METADATA', 'localhost').get('platform')
    if "x86_64-mlnx_msn2700" not in platform_info:
        log.log_warning("Patch is not supported on this platform {}".format(platform_info))
        return False
    
    REQUIRED_SONIC_VERSION = ["20191130.79", "20191130.83", "20191130.84", "20220531.40"]
    sonic_version_info = get_sonic_version_info()
    if sonic_version_info["build_version"] not in REQUIRED_SONIC_VERSION:
        log.log_warning("Patch is not supported on this SONiC version {}".format(sonic_version_info["build_version"]))
        return False
    
    return True
     

def check_pfcwd_stormed():
    CMD = "pfcwd show stats"
    ret, msg = exec_cmd(CMD)
    if ret != 0:
        log.log_warning("Failed to execute command: {}".format(CMD))
        return False
    for line in msg.splitlines():
        if "stormed" in line:
            log.log_warning("Skip applying patch as PFCWD is triggered {}".format(line))
            return True


def get_polling_interval(config_db):
    poll_interval = config_db.get_entry(CONFIG_DB_PFC_WD_TABLE_NAME, "GLOBAL").get(POLL_INTERVAL)
    return int(poll_interval)
     

def update_pfcwd_detection_time_per_port(config_db, port, pfcwd_info):
    pfc_status = config_db.get_entry(PORT_QOS_MAP, port).get('pfc_enable')
    if pfc_status is None:
        log.log_warning("SKIPPED: PFC is not enabled on port: {}".format(port), also_print_to_console=True)
        return

    config_db.mod_entry(CONFIG_DB_PFC_WD_TABLE_NAME, port, pfcwd_info)
    log.log_info("PFC Watchdog detection&restoration time updated to {} on port: {}".format(pfcwd_info['detection_time'], port))


def update_pfcwd_detection_time(config_db):
    enable = config_db.get_entry('DEVICE_METADATA', 'localhost').get('default_pfcwd_status')
    if not enable or enable.lower() != "enable":
        return
    # Get active ports from Config DB
    active_ports = natsorted(list(config_db.get_table('DEVICE_NEIGHBOR').keys()))

    polling_interval = get_polling_interval(config_db)
    # As per mitigation plan, we need to set detection_time and restoration_time to 2x of polling_interval
    multiply = 2
    pfcwd_info = {
        'detection_time': polling_interval * multiply,
        'restoration_time': polling_interval * multiply,
    }

    for port in active_ports:
        update_pfcwd_detection_time_per_port(config_db, port, pfcwd_info)
    
    log.log_notice("Patch is applied")


def main():
    config_db = ConfigDBConnector()
    config_db.connect()
    if not check_sonic_version(config_db):
        return
    # Skip applying patch if PFCWD is triggered
    if check_pfcwd_stormed():
        return
    update_pfcwd_detection_time(config_db)
    # Save config
    config_save()
    

if __name__ == "__main__":
    main()

