# G-Code G1 movement commands (and associated coordinate manipulation)
#
# Copyright (C) 2016-2021  Kevin O'Connor <kevin@koconnor.net>
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import logging
import time
class GCodeMove:
    def __init__(self, config):
        self.printer = printer = config.get_printer()
        printer.register_event_handler("klippy:ready", self._handle_ready)
        printer.register_event_handler("klippy:shutdown", self._handle_shutdown)
        printer.register_event_handler("toolhead:set_position",
                                       self.reset_last_position)
        printer.register_event_handler("toolhead:manual_move",
                                       self.reset_last_position)
        printer.register_event_handler("gcode:command_error",
                                       self.reset_last_position)
        printer.register_event_handler("extruder:activate_extruder",
                                       self._handle_activate_extruder)
        printer.register_event_handler("homing:home_rails_end",
                                       self._handle_home_rails_end)
        self.is_printer_ready = False
        # Register g-code commands
        gcode = printer.lookup_object('gcode')
        self.gcode = gcode
        self.laser_speed = 0.0
        handlers = [
            'G1', 'G20', 'G21',
            'M82', 'M83', 'G90', 'G91', 'G92', 'M220', 'M221',
            'SET_GCODE_OFFSET', 'SAVE_GCODE_STATE', 'RESTORE_GCODE_STATE', 'SWAP_RESUME'
        ]
        for cmd in handlers:
            func = getattr(self, 'cmd_' + cmd)
            desc = getattr(self, 'cmd_' + cmd + '_help', None)
            gcode.register_command(cmd, func, False, desc)
        gcode.register_command('G0', self.cmd_G1)
        gcode.register_command('M114', self.cmd_M114, True)
        gcode.register_command('GET_POSITION', self.cmd_GET_POSITION, True,
                               desc=self.cmd_GET_POSITION_help)
        self.Coord = gcode.Coord
        # G-Code coordinate manipulation
        self.absolute_coord = self.absolute_extrude = True
        self.base_position = [0.0, 0.0, 0.0, 0.0]
        self.last_position = [0.0, 0.0, 0.0, 0.0]
        self.homing_position = [0.0, 0.0, 0.0, 0.0]
        self.homing_position_bak = [0.0, 0.0, 0.0, 0.0]
        self.speed = 25.
        self.speed_factor = 1. / 60.
        self.extrude_factor = 1.
        # G-Code state
        self.saved_states = {}
        self.move_transform = self.move_with_transform = None
        self.position_with_transform = (lambda: [0., 0., 0., 0.])
        self.is_delta = False
        self.is_power_loss = False
        try:
            self.printer_config = config.getsection('printer')
            if self.printer_config and self.printer_config.get("kinematics") == "delta":
                self.is_delta = True
        except Exception as err:
            logging.error(err)
    def _handle_ready(self):
        self.is_printer_ready = True
        if self.move_transform is None:
            toolhead = self.printer.lookup_object('toolhead')
            self.move_with_transform = toolhead.move
            self.position_with_transform = toolhead.get_position
        self.reset_last_position()
    def _handle_shutdown(self):
        if not self.is_printer_ready:
            return
        self.is_printer_ready = False
        logging.info("gcode state: absolute_coord=%s absolute_extrude=%s"
                     " base_position=%s last_position=%s homing_position=%s"
                     " speed_factor=%s extrude_factor=%s speed=%s",
                     self.absolute_coord, self.absolute_extrude,
                     self.base_position, self.last_position,
                     self.homing_position, self.speed_factor,
                     self.extrude_factor, self.speed)
    def _handle_activate_extruder(self):
        self.reset_last_position()
        self.extrude_factor = 1.
        self.base_position[3] = self.last_position[3]
    def _handle_home_rails_end(self, homing_state, rails):
        self.reset_last_position()
        for axis in homing_state.get_axes():
            self.base_position[axis] = self.homing_position[axis]
    def set_move_transform(self, transform, force=False):
        if self.move_transform is not None and not force:
            raise self.printer.config_error(
                "G-Code move transform already specified")
        old_transform = self.move_transform
        if old_transform is None:
            old_transform = self.printer.lookup_object('toolhead', None)
        self.move_transform = transform
        self.move_with_transform = transform.move
        self.position_with_transform = transform.get_position
        return old_transform
    def _get_gcode_position(self):
        p = [lp - bp for lp, bp in zip(self.last_position, self.base_position)]
        p[3] /= self.extrude_factor
        return p
    def _get_gcode_speed(self):
        return self.speed / self.speed_factor
    def _get_gcode_speed_override(self):
        return self.speed_factor * 60.
    def get_status(self, eventtime=None):
        move_position = self._get_gcode_position()
        return {
            'speed_factor': self._get_gcode_speed_override(),
            'speed': self._get_gcode_speed(),
            'extrude_factor': self.extrude_factor,
            'absolute_coordinates': self.absolute_coord,
            'absolute_extrude': self.absolute_extrude,
            'homing_origin': self.Coord(*self.homing_position),
            'position': self.Coord(*self.last_position),
            'gcode_position': self.Coord(*move_position),
        }
    def reset_last_position(self):
        if self.is_printer_ready:
            self.last_position = self.position_with_transform()
    # G-Code movement commands
    def cmd_G1(self, gcmd):
        # Move
        params = gcmd.get_command_parameters()
        try:
            for pos, axis in enumerate('XYZ'):
                if axis in params:
                    v = float(params[axis])
                    if not self.absolute_coord:
                        # value relative to position of last move
                        self.last_position[pos] += v
                    else:
                        # value relative to base coordinate position
                        self.last_position[pos] = v + self.base_position[pos]
            if 'E' in params:
                v = float(params['E']) * self.extrude_factor
                if not self.absolute_coord or not self.absolute_extrude:
                    # value relative to position of last move
                    self.last_position[3] += v
                else:
                    # value relative to base coordinate position
                    self.last_position[3] = v + self.base_position[3]
            if 'F' in params:
                gcode_speed = float(params['F'])
                if gcode_speed <= 0.:
                    raise gcmd.error("Invalid speed in '%s'"
                                     % (gcmd.get_commandline(),))
                self.speed = gcode_speed * self.speed_factor
            if 'S' in params:
                gcode_speed = float(params['S'])
                self.gcode.run_script_from_command('M3 S%s' % gcode_speed)
                self.laser_speed = gcode_speed
            else:
                if self.laser_speed:
                    self.gcode.run_script_from_command('M5')
        except ValueError as e:
            raise gcmd.error("Unable to parse move '%s'"
                             % (gcmd.get_commandline(),))
        self.move_with_transform(self.last_position, self.speed)

    # G-Code coordinate manipulation
    def cmd_G20(self, gcmd):
        # Set units to inches
        raise gcmd.error('Machine does not support G20 (inches) command')
    def cmd_G21(self, gcmd):
        # Set units to millimeters
        pass
    def cmd_M82(self, gcmd):
        # Use absolute distances for extrusion
        self.absolute_extrude = True
    def cmd_M83(self, gcmd):
        # Use relative distances for extrusion
        self.absolute_extrude = False
    def cmd_G90(self, gcmd):
        # Use absolute coordinates
        self.absolute_coord = True
    def cmd_G91(self, gcmd):
        # Use relative coordinates
        self.absolute_coord = False
    def cmd_G92(self, gcmd):
        # Set position
        offsets = [ gcmd.get_float(a, None) for a in 'XYZE' ]
        for i, offset in enumerate(offsets):
            if offset is not None:
                if i == 3:
                    offset *= self.extrude_factor
                self.base_position[i] = self.last_position[i] - offset
        if offsets == [None, None, None, None]:
            self.base_position = list(self.last_position)
    def cmd_M114(self, gcmd):
        # Get Current Position
        p = self._get_gcode_position()
        gcmd.respond_raw("X:%.3f Y:%.3f Z:%.3f E:%.3f" % tuple(p))
    def cmd_M220(self, gcmd):
        # Set speed factor override percentage
        value = gcmd.get_float('S', 100., above=0.) / (60. * 100.)
        self.speed = self._get_gcode_speed() * value
        self.speed_factor = value
    def cmd_M221(self, gcmd):
        # Set extrude factor override percentage
        new_extrude_factor = gcmd.get_float('S', 100., above=0.) / 100.
        last_e_pos = self.last_position[3]
        e_value = (last_e_pos - self.base_position[3]) / self.extrude_factor
        self.base_position[3] = last_e_pos - e_value * new_extrude_factor
        self.extrude_factor = new_extrude_factor
    cmd_SET_GCODE_OFFSET_help = "Set a virtual offset to g-code positions"
    def cmd_SET_GCODE_OFFSET(self, gcmd):
        move_delta = [0., 0., 0., 0.]
        for pos, axis in enumerate('XYZE'):
            offset = gcmd.get_float(axis, None)
            if offset is None:
                offset = gcmd.get_float(axis + '_ADJUST', None)
                if offset is None:
                    continue
                offset += self.homing_position[pos]
            delta = offset - self.homing_position[pos]
            move_delta[pos] = delta
            self.base_position[pos] += delta
            self.homing_position[pos] = offset
        # Move the toolhead the given offset if requested
        if gcmd.get_int('MOVE', 0):
            speed = gcmd.get_float('MOVE_SPEED', self.speed, above=0.)
            for pos, delta in enumerate(move_delta):
                self.last_position[pos] += delta
            self.move_with_transform(self.last_position, speed)
    def cmd_SWAP_RESUME(self, gcmd):
        state = self.saved_states.get("M600_state")
        if state is None:
            self.gcode.run_script_from_command("RESUME")
    def recordPrintFileName(self, path, file_name, fan_state="", filament_used=0, last_print_duration=0):
        import json, os
        fan = ""
        old_filament_used = 0
        old_last_print_duration = 0
        if os.path.exists(path):
            with open(path, "r") as f:
                result = (json.loads(f.read()))
                fan = result.get("fan_state", "")
                old_filament_used = result.get("filament_used", 0)
                old_last_print_duration = result.get("last_print_duration", 0)
        if fan_state and fan_state != fan:
            state = fan_state
        else:
            state = fan
        if filament_used and filament_used != old_filament_used:
            pass
        else:
            filament_used = old_filament_used
        if last_print_duration and last_print_duration != old_last_print_duration:
            pass
        else:
            last_print_duration = old_last_print_duration
        data = {
            'file_path': file_name,
            'absolute_coord': self.absolute_coord,
            'absolute_extrude': self.absolute_extrude,
            'speed_factor': self.speed_factor,
            'extrude_factor': self.extrude_factor,
            'fan_state': state,
            'filament_used': filament_used,
            'last_print_duration': last_print_duration,
        }
        with open(path, "w") as f:
            f.write(json.dumps(data))
            f.flush()
    cmd_CX_SAVE_GCODE_STATE_help = "CX Save G-Code coordinate state"
    # def cmd_CX_SAVE_GCODE_STATE(self, file_position, path, file_name):
    def cmd_CX_SAVE_GCODE_STATE(self, file_position, path, line_pos):
        import json
        from subprocess import call
        data = {
            'file_position': file_position,
            'base_position_e': round(list(self.base_position)[-1], 2),
        }
        cmd = "sed -i %sc'%s' %s" % (line_pos, json.dumps(data), path)
        call(cmd, shell=True)
        # with open(path, "w") as f:
        #     f.write(json.dumps(data))
        #     f.flush()

    cmd_CX_RESTORE_GCODE_STATE_help = "Restore a previously saved G-Code state"
    def cmd_CX_RESTORE_GCODE_STATE(self, path, file_name_path, XYZE):
        try:
            state = {
                "absolute_extrude": True,
                "file_position": 0,
                "extrude_factor": 1.0,
                "speed_factor": 0.0166666,
                "homing_position": [0.0, 0.0, 0.0, 0.0],
                "last_position": [0.0, 0.0, 0.0, 0.0],
                "speed": 25.0,
                "file_path": "",
                "base_position": [0.0, 0.0, 0.0, -0.0],
                "absolute_coord": True,
                "fan_state": "",
                "filament_used": 0,
                "last_print_duration": 0,
                "z_pause": 0,
                "z_toolhead_moved": 0,
            }
            import os, json
            base_position_e = -1
            with open(path, "r") as f:
                ret = f.readlines()
                info = {}
                for obj in ret:
                    obj = obj.strip("'").strip("\n")
                    if len(obj) > 10:
                        obj = eval(obj)
                        if not info:
                            info = obj
                        else:
                            if obj.get("file_position", 0) > info.get("file_position", 0):
                                info = obj
                ret = info
                # ret = json.loads(f.read())
                state["file_position"] = ret.get("file_position", 0)
                state["base_position"] = [0.0, 0.0, 0.0, ret.get("base_position_e", -1)]
                base_position_e = ret.get("base_position_e", -1)
            with open(file_name_path, "r") as f:
                file_info = json.loads(f.read())
                state["file_path"] = file_info.get("file_path", "")
                state["absolute_extrude"] = file_info.get("absolute_extrude", True)
                state["absolute_coord"] = file_info.get("absolute_coord", True)
                state["fan_state"] = file_info.get("fan_state", "")
                state["speed_factor"] = file_info.get("speed_factor", 0.016666666)
                state["extrude_factor"] = file_info.get("extrude_factor", 1.0)
                state["z_pause"] = file_info.get("z_pause", 0)
                state["z_toolhead_moved"] = file_info.get("z_toolhead_moved", 0)
            state["last_position"] = [XYZE["X"], XYZE["Y"], XYZE["Z"], XYZE["E"]+base_position_e]
            logging.info("power_loss cmd_CX_RESTORE_GCODE_STATE state:%s" % str(state))
            logging.info("power_loss cmd_CX_RESTORE_GCODE_STATE self.last_position:%s" % str(self.last_position))

            # Restore state
            self.absolute_coord = state['absolute_coord']
            # self.absolute_extrude = state['absolute_extrude']
            self.base_position = list(state['base_position'])
            logging.info("power_loss cmd_CX_RESTORE_GCODE_STATE base_position:%s" % str(self.base_position))
            self.homing_position_bak = self.homing_position
            self.homing_position = list(state['homing_position'])
            self.speed = state['speed']
            self.speed_factor = state['speed_factor']
            # self.extrude_factor = state['extrude_factor']
            self.extrude_factor = 1.0
            # Restore the relative E position
            if self.is_delta:
                # e_diff = self.last_position[3] - state['last_position'][3] + 1.0
                e_diff = self.last_position[3] - state['last_position'][3] + 6.0
            else:
                # e_diff = self.last_position[3] - state['last_position'][3] - 0.5
                e_diff = self.last_position[3] - state['last_position'][3]- 0.2
            # e_diff = self.last_position[3] - state['last_position'][3]
            self.base_position[3] += e_diff
            # Move the toolhead back if requested
            gcode = self.printer.lookup_object('gcode')
            if state["fan_state"]:
                gcode.run_script(state["fan_state"])
            gcode.run_script("BED_MESH_SET_DISABLE")
            if self.is_delta:
                gcode.run_script("G28")
            else:
                gcode.run_script("G28 X0 Y0")
            toolhead = self.printer.lookup_object("toolhead")
            logging.info("power_loss cmd_CX_RESTORE_GCODE_STATE X:%s Y:%s" % (state['last_position'][0], state['last_position'][1]))
            if not self.is_delta:
                gcode.run_script("G0 X%s Y%s F16000" % (state['last_position'][0], state['last_position'][1]))
            x = self.last_position[0]
            y = self.last_position[1]
            z = self.last_position[2] + state['last_position'][2] + state["z_pause"] + state["z_toolhead_moved"] + 0.05
            cfgname = self.printer.get_start_args()['config_file']
            printer_name = ""
            with open(cfgname, "r") as f:
                printer_name = str(f.readline())
            if "Ender-3 V3 SE" in printer_name:
                z += 5
            logging.info("power_loss cmd_CX_RESTORE_GCODE_STATE x:%s y:%s z:%s self.last_position[2]:%s state['last_position'][2]:%s z_pause:%s" % (x,y,z,self.last_position[2],state['last_position'][2], state["z_pause"]))
            toolhead = self.printer.lookup_object("toolhead")
            if self.is_delta:
                cur_x, cur_y, cur_z, cur_e = toolhead.get_position()
                logging.info("power_loss cmd_CX_RESTORE_GCODE_STATE get cur position x:%s y:%s z:%s" % (cur_x,cur_y,cur_z))
                # toolhead.set_position([cur_x, cur_y, self.last_position[2], self.last_position[3]], homing_axes=(2,))
                self.is_power_loss = True
            else:
                toolhead.set_position([x, y, z, self.last_position[3]], homing_axes=(2,))
            # toolhead.set_position([x, y, z, self.last_position[3]], homing_axes=(2,))
            speed = self.speed
            self.last_position[:3] = state['last_position'][:3]
            if self.is_delta:
                self.last_position[2] = state['last_position'][2]
                z_pos = self.last_position[2]
                logging.info("power_loss cmd_CX_RESTORE_GCODE_STATE move_with_transform: xyz%s" % str(self.last_position))
                self.move_with_transform(self.last_position, speed)
                # self.move_with_transform([cur_x, cur_y, z, state['last_position'][3]], speed)
            else:
                self.move_with_transform(self.last_position, speed)
            # self.move_with_transform(self.last_position, speed)
            gcode.run_script("G0 X%s Y%s F3000" % (state['last_position'][0], state['last_position'][1]))
            gcode.run_script("M400")
            if self.is_delta:
                cur_x2, cur_y2, cur_z2, cur_e2 = toolhead.get_position()
                logging.info("power_loss  cur_x2:%s y:%s z:%s" % (cur_x2,cur_y2,cur_z2))
                # down_step = abs(self.homing_position_bak[2]) - 0.5 if abs(self.homing_position_bak[2]) - 0.5 > 0 else 0
                down_step = abs(self.homing_position_bak[2]) + 0.2
                gcode.run_script("G91\nG0 Z-%s\nG90" % down_step)
                cur_x3, cur_y3, cur_z3, cur_e3 = toolhead.get_position()
                logging.info("power_loss  cur_x3:%s y:%s z:%s" % (cur_x3,cur_y3,cur_z3))
                logging.info("power_loss set_position x:%s y:%s z:%s" % (state['last_position'][0], state['last_position'][1], z_pos))
                toolhead.set_position([state['last_position'][0], state['last_position'][1], z_pos, self.last_position[3]], homing_axes=(2,))
                cur_x4, cur_y4, cur_z4, cur_e4 = toolhead.get_position()
                logging.info("power_loss  cur_x4:%s y:%s z:%s" % (cur_x4,cur_y4,cur_z4))
                gcode.run_script("M400")
            self.absolute_extrude = state['absolute_extrude']
            gcode.run_script("M221 S%s" % int(state['extrude_factor']*100))
            try:
                if os.path.exists(gcode.exclude_object_info):
                    with open(gcode.exclude_object_info, "r") as f:
                        exclude_object_cmds = json.loads(f.read())
                        EXCLUDE_OBJECT_DEFINE = exclude_object_cmds.get("EXCLUDE_OBJECT_DEFINE", [])
                        EXCLUDE_OBJECT = exclude_object_cmds.get("EXCLUDE_OBJECT", [])
                        for line in EXCLUDE_OBJECT_DEFINE:
                            gcode.run_script_from_command(line)
                        for line in EXCLUDE_OBJECT:
                            gcode.run_script_from_command(line)
                        gcode.run_script_from_command("M400")
            except Exception as err:
                logging.exception("RESTORE EXCLUDE_OBJECT err:%s" % err)
        except Exception as err:
            logging.exception("cmd_CX_RESTORE_GCODE_STATE err:%s" % err)
    cmd_SAVE_GCODE_STATE_help = "Save G-Code coordinate state"
    def cmd_SAVE_GCODE_STATE(self, gcmd):
        state_name = gcmd.get('NAME', 'default')
        self.saved_states[state_name] = {
            'absolute_coord': self.absolute_coord,
            'absolute_extrude': self.absolute_extrude,
            'base_position': list(self.base_position),
            'last_position': list(self.last_position),
            'homing_position': list(self.homing_position),
            'speed': self.speed, 'speed_factor': self.speed_factor,
            'extrude_factor': self.extrude_factor,
        }
    cmd_RESTORE_GCODE_STATE_help = "Restore a previously saved G-Code state"
    def cmd_RESTORE_GCODE_STATE(self, gcmd):
        state_name = gcmd.get('NAME', 'default')
        state = self.saved_states.get(state_name)
        if state is None:
            raise gcmd.error("Unknown g-code state: %s" % (state_name,))
        # Restore state
        self.absolute_coord = state['absolute_coord']
        self.absolute_extrude = state['absolute_extrude']
        self.base_position = list(state['base_position'])
        self.homing_position = list(state['homing_position'])
        self.speed = state['speed']
        self.speed_factor = state['speed_factor']
        self.extrude_factor = state['extrude_factor']
        # Restore the relative E position
        e_diff = self.last_position[3] - state['last_position'][3]
        self.base_position[3] += e_diff
        # Move the toolhead back if requested
        if gcmd.get_int('MOVE', 0):
            speed = gcmd.get_float('MOVE_SPEED', self.speed, above=0.)
            self.last_position[:3] = state['last_position'][:3]
            self.move_with_transform(self.last_position, speed)
    cmd_GET_POSITION_help = (
        "Return information on the current location of the toolhead")
    def cmd_GET_POSITION(self, gcmd):
        toolhead = self.printer.lookup_object('toolhead', None)
        if toolhead is None:
            raise gcmd.error("Printer not ready")
        kin = toolhead.get_kinematics()
        steppers = kin.get_steppers()
        mcu_pos = " ".join(["%s:%d" % (s.get_name(), s.get_mcu_position())
                            for s in steppers])
        cinfo = [(s.get_name(), s.get_commanded_position()) for s in steppers]
        stepper_pos = " ".join(["%s:%.6f" % (a, v) for a, v in cinfo])
        kinfo = zip("XYZ", kin.calc_position(dict(cinfo)))
        kin_pos = " ".join(["%s:%.6f" % (a, v) for a, v in kinfo])
        toolhead_pos = " ".join(["%s:%.6f" % (a, v) for a, v in zip(
            "XYZE", toolhead.get_position())])
        gcode_pos = " ".join(["%s:%.6f"  % (a, v)
                              for a, v in zip("XYZE", self.last_position)])
        base_pos = " ".join(["%s:%.6f"  % (a, v)
                             for a, v in zip("XYZE", self.base_position)])
        homing_pos = " ".join(["%s:%.6f"  % (a, v)
                               for a, v in zip("XYZ", self.homing_position)])
        gcmd.respond_info("mcu: %s\n"
                          "stepper: %s\n"
                          "kinematic: %s\n"
                          "toolhead: %s\n"
                          "gcode: %s\n"
                          "gcode base: %s\n"
                          "gcode homing: %s"
                          % (mcu_pos, stepper_pos, kin_pos, toolhead_pos,
                             gcode_pos, base_pos, homing_pos))

def load_config(config):
    return GCodeMove(config)
