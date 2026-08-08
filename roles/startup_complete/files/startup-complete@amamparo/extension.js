// Put an app in the window switcher as soon as it actually has a window.
//
// Without this, apps can be missing from alt+tab for up to 15 seconds AFTER
// their window is already on screen and usable (the pointer sits in the
// "launching" spinner for the same stretch). The chain, all upstream code:
//
//   1. gnome-shell launches through mutter's MetaLaunchContext, which opens a
//      startup sequence UNCONDITIONALLY — it never consults the desktop entry's
//      StartupNotify key, so turning that off does nothing here. (Only
//      GdkAppLaunchContext honours it, which is why launching the same app with
//      gtk-launch behaves and clicking its icon does not.)
//   2. The app is expected to finish the sequence by activating with the
//      XDG_ACTIVATION_TOKEN it was handed. GTK does this for free; Chromium and
//      Electron apps do not, so the sequence just sits there.
//   3. gnome-shell pins the app in SHELL_APP_STATE_STARTING while its sequence
//      is live: shell_app_sync_running_state() promotes to RUNNING only
//      `if (app->state != SHELL_APP_STATE_STARTING)`.
//   4. The switcher is built from shell_app_system_get_running(), whose
//      running_apps table only ever receives RUNNING apps — so a STARTING app
//      is absent entirely, not merely iconless.
//   5. mutter finally force-completes the sequence at STARTUP_TIMEOUT_MS,
//      15000 ms in src/core/startup-notification.c, swept by a 1 Hz timer.
//
// So do what the app should have done: complete the sequence once its app has
// produced a window. This is the ordinary completion path, not a bypass —
// meta_startup_sequence_complete() emits SEQ_COMPLETE, MetaStartupNotification
// turns that into its "changed" signal, and gnome-shell re-evaluates the app to
// RUNNING exactly as it would have at the timeout. All this changes is when.

import GLib from 'gi://GLib';
import Shell from 'gi://Shell';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';

export default class StartupCompleteExtension extends Extension {
    enable() {
        this._tracker = Shell.WindowTracker.get_default();
        this._appSystem = Shell.AppSystem.get_default();
        this._sweepId = 0;

        // Deliberately does NOT inspect the new window: 'window-created' fires
        // before shell-window-tracker has associated it with an app, and signal
        // handler order is not guaranteed. Queue instead, so the sweep runs once
        // every handler for this emission has had its turn.
        global.display.connectObject('window-created',
            () => this._queueSweep(), this);

        // Anything already pending when we load — at login the shell is up
        // before session-restored apps have mapped their windows.
        this._queueSweep();
    }

    disable() {
        global.display.disconnectObject(this);
        if (this._sweepId) {
            GLib.source_remove(this._sweepId);
            this._sweepId = 0;
        }
        this._tracker = null;
        this._appSystem = null;
    }

    _queueSweep() {
        if (this._sweepId)
            return;

        this._sweepId = GLib.idle_add(GLib.PRIORITY_DEFAULT_IDLE, () => {
            this._sweepId = 0;
            this._sweep();
            return GLib.SOURCE_REMOVE;
        });
    }

    _sweep() {
        for (const sequence of this._tracker.get_startup_sequences()) {
            if (sequence.get_completed())
                continue;

            // Sequences raised by non-launcher paths can carry no application
            // id; there is nothing to match those against, and they time out
            // harmlessly on their own.
            const id = sequence.get_application_id();
            if (!id)
                continue;

            const app = this._appSystem.lookup_app(id);
            if (app && app.get_n_windows() > 0)
                sequence.complete();
        }
    }
}
