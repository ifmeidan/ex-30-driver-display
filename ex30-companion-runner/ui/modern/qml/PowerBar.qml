import QtQuick
import EX30 1.0

// Center-zero power gauge, segmented ticks: blue up under load, green down
// during regen (7 up + 7 down from center).
Item {
    id: bar
    width: 120
    height: 480

    property real shownUp: vehicle.throttlePct
    property real shownDown: vehicle.regenPct
    property real shownKw: vehicle.powerKw
    Behavior on shownUp   { NumberAnimation { duration: 110 } }
    Behavior on shownDown { NumberAnimation { duration: 110 } }
    Behavior on shownKw   { NumberAnimation { duration: 150 } }

    readonly property real trackY: 106
    readonly property real trackH: 244
    readonly property real centerY: trackY + trackH / 2
    readonly property real halfH: trackH / 2
    readonly property bool regen: shownKw < -0.3
    readonly property bool draw: shownKw > 0.3

    // kW readout
    Item {
        anchors.horizontalCenter: parent.horizontalCenter
        y: 30
        width: 160
        height: 60

        Text {
            id: kwValue
            anchors.horizontalCenter: parent.horizontalCenter
            anchors.baseline: parent.bottom
            // Whole kW only — the .1f decimal wiggled constantly at low power.
            text: Math.floor(Math.abs(bar.shownKw))
            color: bar.regen ? Theme.green : bar.draw ? Theme.blue : Theme.subtext
            font.family: Theme.sans
            font.pixelSize: 42
            font.weight: Font.Medium
            font.features: ({ "tnum": 1 })
            Behavior on color { ColorAnimation { duration: 200 } }
        }
        Text {
            anchors.left: kwValue.right
            anchors.leftMargin: 7
            anchors.baseline: kwValue.baseline
            text: "kW"
            color: Theme.subtext
            font.family: Theme.sans
            font.pixelSize: 19
        }
    }

    // ── Segmented ticks, 7 up + 7 down from center ───────────────
    Item {
        anchors.fill: parent

        readonly property real gap: 5.5

        Repeater {   // draw, growing upward
            model: 7
            Rectangle {
                required property int index
                readonly property real frac:
                    Math.max(0, Math.min(1, bar.shownUp * 7 - index))
                anchors.horizontalCenter: parent.horizontalCenter
                y: bar.centerY - 8 - (index + 1) * 11 - index * parent.gap
                width: 18
                height: 11
                radius: 3.5
                color: frac > 0 ? Theme.blue : Theme.faint
                opacity: frac > 0 ? 0.30 + 0.70 * frac : 0.30
            }
        }
        Repeater {   // regen, growing downward
            model: 7
            Rectangle {
                required property int index
                readonly property real frac:
                    Math.max(0, Math.min(1, bar.shownDown * 7 - index))
                anchors.horizontalCenter: parent.horizontalCenter
                y: bar.centerY + 8 + index * (11 + parent.gap)
                width: 18
                height: 11
                radius: 3.5
                color: frac > 0 ? Theme.green : Theme.faint
                opacity: frac > 0 ? 0.30 + 0.70 * frac : 0.30
            }
        }
    }

    // Zero marker
    Rectangle {
        anchors.horizontalCenter: parent.horizontalCenter
        y: bar.centerY - 1
        width: 26
        height: 2
        color: Theme.subtext
        opacity: 0.7
    }

    Text {
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.horizontalCenterOffset: font.letterSpacing / 2
        y: bar.trackY + bar.trackH + 20
        text: "POWER"
        color: bar.regen ? Theme.green : bar.draw ? Theme.blue : Theme.subtext
        opacity: (bar.draw || bar.regen) ? 1 : 0.55
        font.family: Theme.sans
        font.pixelSize: 15
        font.weight: Font.DemiBold
        font.letterSpacing: 2.5
        Behavior on color { ColorAnimation { duration: 200 } }
    }
}
