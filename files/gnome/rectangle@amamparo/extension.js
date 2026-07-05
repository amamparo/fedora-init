// Rectangle-style tiling for GNOME Shell 48+.
//
// Super+Alt+Left/Right/Up/Down snaps the focused window to that edge of the
// current monitor's work area. Repeated presses on the same edge cycle the
// window through 1/2 -> 2/3 -> 1/3 of the span, like Rectangle on macOS.

import Meta from 'gi://Meta';
import Shell from 'gi://Shell';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';

const CYCLE = [1 / 2, 2 / 3, 1 / 3];
const KEYS = ['tile-left', 'tile-right', 'tile-up', 'tile-down'];

export default class RectangleExtension extends Extension {
    enable() {
        this._settings = this.getSettings();
        for (const key of KEYS) {
            Main.wm.addKeybinding(
                key,
                this._settings,
                Meta.KeyBindingFlags.IGNORE_AUTOREPEAT,
                Shell.ActionMode.NORMAL,
                () => this._tile(key.replace('tile-', ''))
            );
        }
    }

    disable() {
        for (const key of KEYS)
            Main.wm.removeKeybinding(key);
        this._settings = null;
    }

    _tile(direction) {
        const win = global.display.get_focus_window();
        if (!win || win.get_window_type() !== Meta.WindowType.NORMAL || !win.allows_resize())
            return;

        if (win.is_fullscreen())
            win.unmake_fullscreen();
        this._unmaximize(win);

        const area = win.get_work_area_current_monitor();
        const frame = win.get_frame_rect();
        const horizontal = direction === 'left' || direction === 'right';
        const span = horizontal ? area.width : area.height;
        const tolerance = Math.max(2, Math.round(span * 0.02));

        // If the window already sits on this edge at one of the cycle sizes,
        // advance to the next size; otherwise start the cycle at 1/2.
        let next = 0;
        for (let i = 0; i < CYCLE.length; i++) {
            if (this._snapped(direction, frame, area, CYCLE[i], tolerance)) {
                next = (i + 1) % CYCLE.length;
                break;
            }
        }

        const size = Math.round(span * CYCLE[next]);
        let [x, y, width, height] = [area.x, area.y, area.width, area.height];
        switch (direction) {
        case 'left':
            width = size;
            break;
        case 'right':
            width = size;
            x = area.x + area.width - size;
            break;
        case 'up':
            height = size;
            break;
        case 'down':
            height = size;
            y = area.y + area.height - size;
            break;
        }
        win.move_resize_frame(true, x, y, width, height);
    }

    _snapped(direction, frame, area, fraction, tol) {
        const eq = (a, b) => Math.abs(a - b) <= tol;
        switch (direction) {
        case 'left':
            return eq(frame.x, area.x) &&
                eq(frame.width, area.width * fraction);
        case 'right':
            return eq(frame.x + frame.width, area.x + area.width) &&
                eq(frame.width, area.width * fraction);
        case 'up':
            return eq(frame.y, area.y) &&
                eq(frame.height, area.height * fraction);
        case 'down':
            return eq(frame.y + frame.height, area.y + area.height) &&
                eq(frame.height, area.height * fraction);
        }
        return false;
    }

    _unmaximize(win) {
        try {
            win.unmaximize();                        // GNOME 49+ API
        } catch (e) {
            win.unmaximize(Meta.MaximizeFlags.BOTH); // GNOME <= 48 API
        }
    }
}
