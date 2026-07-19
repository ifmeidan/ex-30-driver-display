import QtQuick
import EX30 1.0

// One connection line on the loading screen: indicator + name + live state.
Item {
    id: row
    property string title: ""
    property string subtitle: ""
    property string linkState: "wait"   // wait | ok | fail

    width: 460
    height: 84

    // Indicator
    Item {
        id: indicator
        width: 34
        height: 34
        anchors.verticalCenter: parent.verticalCenter

        // waiting: quietly pulsing hollow ring
        Rectangle {
            anchors.centerIn: parent
            width: 22; height: 22; radius: 11
            color: "transparent"
            border.width: 2
            border.color: Theme.bootText
            visible: row.linkState === "wait"
            opacity: 0.5
            SequentialAnimation on opacity {
                running: row.linkState === "wait"
                loops: Animation.Infinite
                NumberAnimation { from: 0.22; to: 0.75; duration: 700; easing.type: Easing.InOutSine }
                NumberAnimation { from: 0.75; to: 0.22; duration: 700; easing.type: Easing.InOutSine }
            }
        }

        // ok / fail: filled disc with glyph
        Rectangle {
            anchors.centerIn: parent
            width: 30; height: 30; radius: 15
            color: row.linkState === "ok" ? Theme.green : Theme.amber
            visible: row.linkState === "ok" || row.linkState === "fail"
            scale: visible ? 1 : 0.4
            Behavior on scale {
                NumberAnimation { duration: 260; easing.type: Easing.OutBack }
            }
            Text {
                anchors.centerIn: parent
                text: row.linkState === "ok" ? "✓" : "!"
                color: "#0B0B0D"
                font.family: Theme.sans
                font.pixelSize: 17
                font.weight: Font.Bold
            }
        }
    }

    Column {
        anchors.left: indicator.right
        anchors.leftMargin: 22
        anchors.verticalCenter: parent.verticalCenter
        spacing: 5

        Text {
            text: row.title
            color: Theme.bootText
            font.family: Theme.sans
            font.pixelSize: 25
            font.weight: Font.DemiBold
            font.letterSpacing: Theme.trackingCaps
        }
        Text {
            text: row.subtitle
            color: Theme.bootText
            opacity: 0.45
            font.family: Theme.sans
            font.pixelSize: 17
            font.letterSpacing: 0.5
        }
    }

    Text {
        anchors.right: parent.right
        anchors.verticalCenter: parent.verticalCenter
        text: row.linkState === "ok" ? "CONNECTED"
            : row.linkState === "fail" ? "OFFLINE" : "SEARCHING"
        color: row.linkState === "ok" ? Theme.green
             : row.linkState === "fail" ? Theme.amber : Theme.bootText
        opacity: row.linkState === "wait" ? 0.4 : 1
        font.family: Theme.sans
        font.pixelSize: 16
        font.weight: Font.DemiBold
        font.letterSpacing: 2.5
    }
}
