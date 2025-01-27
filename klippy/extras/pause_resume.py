# Pause/Resume functionality with position capture/restore
#
# Copyright (C) 2019  Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.
from .tool import reportInformation

class PauseResume:
    def __init__(self, config):
        self.config = config
        self.printer = config.get_printer()
        self.gcode = self.printer.lookup_object('gcode')
        self.recover_velocity = config.getfloat('recover_velocity', 50.)
        self.v_sd = None
        self.is_paused = False
        self.sd_paused = False
        self.pause_command_sent = False
        self.printer.register_event_handler("klippy:connect",
                                            self.handle_connect)
        self.gcode.register_command("PAUSE", self.cmd_PAUSE,
                                    desc=self.cmd_PAUSE_help)
        self.gcode.register_command("M600", self.cmd_M600,
                                    desc=self.cmd_M600_help)
        self.gcode.register_command("RESUME", self.cmd_RESUME,
                                    desc=self.cmd_RESUME_help)
        self.gcode.register_command("CLEAR_PAUSE", self.cmd_CLEAR_PAUSE,
                                    desc=self.cmd_CLEAR_PAUSE_help)
        self.gcode.register_command("CANCEL_PRINT", self.cmd_CANCEL_PRINT,
                                    desc=self.cmd_CANCEL_PRINT_help)
        webhooks = self.printer.lookup_object('webhooks')
        webhooks.register_endpoint("pause_resume/cancel",
                                   self._handle_cancel_request)
        webhooks.register_endpoint("pause_resume/pause",
                                   self._handle_pause_request)
        webhooks.register_endpoint("pause_resume/resume",
                                   self._handle_resume_request)
        webhooks.register_endpoint("extruder_rotation_distance",
                                   self._handle_extruder_rotation_distance_request)
        webhooks.register_endpoint("extruder_gear_ratio",
                                   self._handle_extruder_gear_ratio_request)
        webhooks.register_endpoint("set_extruder_rotation_distance",
                                   self._set_extruder_rotation_distance_request)
        webhooks.register_endpoint("set_extruder_gear_ratio",
                                   self._set_extruder_gear_ratio_request)

    def _handle_extruder_rotation_distance_request(self, web_request):
        # self.autosave.fileconfig.set(section, option, svalue)
        rotation_dist = 32.473
        if self.config.has_section("extruder"):
            rotation_dist = self.config.getsection("extruder").getfloat('rotation_distance', above=0., note_valid=False)
        result = {"code": 200, "rotation_distance": rotation_dist}
        web_request.send(result)
        return result

    def _handle_extruder_gear_ratio_request(self, web_request):
        gear_ratio = [1.0, 1.0]
        try:
            if self.config.has_section("extruder"):
                gear_ratio = self.config.getsection("extruder").getlists('gear_ratio', (1.0, 1.0), seps=(':', ','),
                                                                         count=2, parser=float, note_valid=False)
                if isinstance(gear_ratio[0], tuple):
                    gear_ratio = gear_ratio[0]
        except Exception as err:
            import logging
            logging.error(err)
        result = {"code": 200, "gear_ratio": {"Molecule": gear_ratio[0], "Denominator": gear_ratio[1]}}
        web_request.send(result)
        return result

    def _set_extruder_rotation_distance_request(self, web_request):
        rotation_distance = web_request.get("rotation_distance", 32.473)
        if self.config.has_section("extruder"):
            self.printer.lookup_object('gcode').run_script(
                "SET_ROTATION_DISTANCE ROTATION_DISTANCE=%s" % rotation_distance)
        result = {"code": 200, "rotation_distance": float(rotation_distance)}
        web_request.send(result)
        import threading
        t = threading.Thread(target=self.request_restart)
        t.start()
        return result

    def _set_extruder_gear_ratio_request(self, web_request):
        Molecule = web_request.get("Molecule", 1.0)
        Denominator = web_request.get("Denominator", 1.0)
        gear_ratio = "%s:%s" % (Molecule, Denominator)
        if self.config.has_section("extruder"):
            self.printer.lookup_object('gcode').run_script("SET_GEAR_RATIO GEAR_RATIO=%s" % gear_ratio)
        result = {"code": 200, "Molecule": float(Molecule), "Denominator": float(Denominator)}
        web_request.send(result)
        import threading
        t = threading.Thread(target=self.request_restart)
        t.start()
        return result

    def request_restart(self):
        import time
        time.sleep(1)
        gcode = self.printer.lookup_object('gcode')
        gcode.request_restart('restart')

    def handle_connect(self):
        self.v_sd = self.printer.lookup_object('virtual_sdcard', None)

    def _handle_cancel_request(self, web_request):
        self.gcode.run_script("CANCEL_PRINT")

    def _handle_pause_request(self, web_request):
        self.v_sd.power_loss_pause_flag = True
        self.gcode.run_script("PAUSE")
        self.v_sd.power_loss_pause_flag = False

    def _handle_resume_request(self, web_request):
        self.gcode.run_script("RESUME")

    def get_status(self, eventtime):
        return {
            'is_paused': self.is_paused
        }

    def is_sd_active(self):
        return self.v_sd is not None and self.v_sd.is_active()

    def send_pause_command(self):
        # This sends the appropriate pause command from an event.  Note
        # the difference between pause_command_sent and is_paused, the
        # module isn't officially paused until the PAUSE gcode executes.
        if not self.pause_command_sent:
            if self.is_sd_active():
                # Printing from virtual sd, run pause command
                self.sd_paused = True
                self.v_sd.do_pause()
            else:
                self.sd_paused = False
                self.gcode.respond_info("action:paused")
            self.pause_command_sent = True
            self.printer.lookup_object('toolhead').move_queue.flush()

    cmd_PAUSE_help = ("Pauses the current print")

    def cmd_PAUSE(self, gcmd):
        import os, time
        reactor = self.printer.get_reactor()
        count = 0
        # while True:
        while self.v_sd.toolhead_moved:
            # count += 1
            # if not self.v_sd.toolhead_moved or count > 500:
            #     break
            time.sleep(0.001)
            reactor.pause(reactor.monotonic() + .01)
        if self.is_paused:
            gcmd.respond_info("""{"code":"key211", "msg": "Print already paused", "values": []}""")
            return
        self.send_pause_command()
        self.gcode.run_script_from_command("SAVE_GCODE_STATE NAME=PAUSE_STATE")
        self.is_paused = True
        reportInformation("key601,")
        mcu = self.printer.lookup_object('mcu', None)
        pre_serial = mcu._serial.serial_dev.port.split("/")[-1]
        print_file_name_save_path = "/mnt/UDISK/%s_print_file_name.save" % pre_serial
        if os.path.exists(print_file_name_save_path):
            try:
                import json
                result = {}
                with open(print_file_name_save_path, "r") as f:
                    result = json.loads(f.read())
                    result["z_pause"] = 2
                with open(print_file_name_save_path, "w") as f:
                    f.write(json.dumps(result))
                    f.flush()
            except Exception as err:
                pass

    def send_resume_command(self):
        if self.sd_paused:
            # Printing from virtual sd, run pause command
            self.v_sd.do_resume_status = True
            self.v_sd.do_resume()
            self.sd_paused = False
        else:
            self.gcode.respond_info("action:resumed")
        self.pause_command_sent = False
        self.printer.lookup_object('toolhead').move_queue.flush()
    cmd_RESUME_help = ("Resumes the print from a pause")
    def cmd_RESUME(self, gcmd):
        if not self.is_paused and not self.v_sd.lida_paused:
            gcmd.respond_info("""{"code": "key16", "msg": "Print is not paused, resume aborted"}""")
            return
        velocity = gcmd.get_float('VELOCITY', self.recover_velocity)
        self.gcode.run_script_from_command(
            "RESTORE_GCODE_STATE NAME=PAUSE_STATE MOVE=1 MOVE_SPEED=%.4f"
            % (velocity))
        self.send_resume_command()
        self.is_paused = False
        self.v_sd.lida_paused = False
        reportInformation("key602,")

    cmd_M600_help = ("M600 Pauses the current print")

    def cmd_M600(self, gcmd):
        x = gcmd.get_float("X", 0.)
        y = gcmd.get_float("Y", 0.)
        z = gcmd.get_float("Z", 10.)
        e = gcmd.get_float("E", -20.)
        if self.is_paused:
            gcmd.respond_info("""{"code":"key211", "msg": "Print already paused", "values": []}""")
            return
        self.send_pause_command()
        self.gcode.run_script_from_command("SAVE_GCODE_STATE NAME=M600_state\n")
        self.gcode.run_script_from_command("SAVE_GCODE_STATE NAME=PAUSE_STATE")
        self.gcode.run_script_from_command(
            "G91\n"
            "G1 E-5 F4000\n"
            "G1 Z%s\n"
            "G90\n"
            "G1 X%s Y%s F3000\n"
            "G0 E10 F6000\n"
            "G0 E%s F6000\n"
            "G92 E0" % (z, x, y, e))
        self.is_paused = True

    cmd_CLEAR_PAUSE_help = (
        "Clears the current paused state without resuming the print")

    def cmd_CLEAR_PAUSE(self, gcmd):
        self.is_paused = self.pause_command_sent = False
    cmd_CANCEL_PRINT_help = ("Cancel the current print")
    def cmd_CANCEL_PRINT(self, gcmd):
        if self.is_sd_active() or self.sd_paused:
            self.v_sd.do_cancel()
        else:
            gcmd.respond_info("action:cancel")
        self.cmd_CLEAR_PAUSE(gcmd)
        self.v_sd.cancel_print_state = False
        self.v_sd.pause_flag = 1
        reportInformation("key603,")


def load_config(config):
    return PauseResume(config)
