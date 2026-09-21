// Start the session on the desktop: GNOME Shell (40+) opens the Activities
// overview at every login and has no setting to turn that off (the MR making
// it configurable, gnome-shell!4009, is unmerged as of GNOME 50).
//
// Since GNOME 50, extensions initialize only after the startup animation has
// begun, so the overview can't be *prevented* — it flashes briefly and is
// hidden the moment startup completes. Hooking 'startup-complete' is the
// only mechanism that still works on 50 (sessionMode.hasOverview overrides
// died with the 50 startup reordering). If enabled mid-session the signal
// simply never fires again, so this is a safe no-op on re-enable.

import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';

export default class NoOverviewExtension extends Extension {
    enable() {
        Main.layoutManager.connectObject('startup-complete',
            () => Main.overview.hide(), this);
    }

    disable() {
        Main.layoutManager.disconnectObject(this);
    }
}
