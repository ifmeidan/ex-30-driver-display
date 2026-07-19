pragma Singleton
import QtQuick

// Design tokens for the modern display. `day` and `sans` are pushed in by
// Main.qml (day tracks vehicle.isDay; sans is the vendored Inter family).
QtObject {
    property bool day: false
    property string sans: "Helvetica Neue"

    // Scandinavian palette — warm paper white by day, near-black by night.
    readonly property color bg:       day ? "#F7F6F3" : "#0B0B0D"
    readonly property color text:     day ? "#17181A" : "#F1F0EE"
    readonly property color subtext:  day ? "#82817E" : "#84848B"
    readonly property color faint:    day ? "#D9D7D2" : "#2A2A30"
    readonly property color hairline: day ? "#E7E5E0" : "#222228"

    // Accents (shared across themes, tuned for both backgrounds)
    readonly property color green:  day ? "#2E9E52" : "#3DBE63"
    readonly property color blue:   day ? "#2F7CDB" : "#4D96EC"
    readonly property color red:    day ? "#DE3A2E" : "#EF5348"
    readonly property color amber:  day ? "#E89B12" : "#F5B02E"

    // Boot/loading are always cinematic-dark regardless of theme.
    readonly property color bootBg:   "#0B0B0D"
    readonly property color bootText: "#E9E9E7"

    // Type scale helpers
    readonly property real trackingCaps: 3.2   // letter-spacing for caps labels

    function fmtThousands(v) {
        return String(Math.round(v)).replace(/\B(?=(\d{3})+(?!\d))/g, " ")
    }
}
