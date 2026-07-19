import QtQuick
import EX30 1.0

// Connection gate between the boot splash and the live dash. Shows OBD and
// AAOS link state; advances the moment both are up (min 1.4s on screen) or
// after 30s regardless (AAOS can lag) — links keep retrying in background.
Item {
    id: loading
    property bool active: false
    signal ready()

    readonly property bool obdOk: vehicle.obdState === "connected"
    readonly property bool obdFail: vehicle.obdState === "failed"
    readonly property bool aaosOk: vehicle.aaosConnected

    property real minMs: 1400
    property real maxMs: 30000
    property double t0: 0

    visible: opacity > 0.001
    opacity: active ? 1 : 0
    Behavior on opacity {
        NumberAnimation { duration: 450; easing.type: Easing.InOutQuad }
    }

    onActiveChanged: {
        if (active) {
            t0 = Date.now()
            gate.start()
        } else {
            gate.stop()
        }
    }

    Timer {
        id: gate
        interval: 100
        repeat: true
        onTriggered: {
            var elapsed = Date.now() - loading.t0
            if ((loading.obdOk && loading.aaosOk && elapsed >= loading.minMs)
                    || elapsed >= loading.maxMs) {
                gate.stop()
                loading.ready()
            }
        }
    }

    IronMark {
        id: smallMark
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.horizontalCenterOffset: size * 0.05   // center the circle
        y: 36
        size: 118
        color: Theme.bootText
    }

    Signature {
        anchors.right: parent.right
        anchors.rightMargin: 72
        anchors.bottom: parent.bottom
        anchors.bottomMargin: 34
    }

    Row {
        anchors.horizontalCenter: parent.horizontalCenter
        y: 218
        spacing: 72

        StatusRow {
            title: "VEHICLE DATA"
            subtitle: "OBD-II · vLinker MC+"
            linkState: loading.obdOk ? "ok" : (loading.obdFail ? "fail" : "wait")
        }
        StatusRow {
            title: "CABIN LINK"
            subtitle: "AAOS bridge · :7878"
            linkState: loading.aaosOk ? "ok" : "wait"
        }
    }

    // Progress hairline — fills over the 30s budget, snaps ahead when done
    Item {
        anchors.horizontalCenter: parent.horizontalCenter
        y: 420
        width: 560
        height: 2

        Rectangle {
            anchors.fill: parent
            radius: 1
            color: Theme.bootText
            opacity: 0.14
        }
        Rectangle {
            id: progressFill
            height: parent.height
            radius: 1
            color: Theme.bootText
            opacity: 0.75
            width: 0
            NumberAnimation on width {
                running: loading.active
                from: 0; to: 560
                duration: loading.maxMs
            }
        }
    }
}
