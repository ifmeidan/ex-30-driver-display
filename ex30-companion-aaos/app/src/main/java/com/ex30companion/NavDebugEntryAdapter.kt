package com.ex30companion

import android.content.Context
import android.view.LayoutInflater
import android.view.ViewGroup
import androidx.core.view.isVisible
import androidx.recyclerview.widget.DiffUtil
import androidx.recyclerview.widget.ListAdapter
import androidx.recyclerview.widget.RecyclerView
import com.ex30companion.databinding.NavDebugEntryBinding
import java.text.DateFormat
import java.util.Date
import java.util.Locale

class NavDebugEntryAdapter : ListAdapter<NavDebugEntry, NavDebugEntryAdapter.Row>(Diff) {
    class Row(val binding: NavDebugEntryBinding) : RecyclerView.ViewHolder(binding.root)

    private val timestamp = DateFormat.getDateTimeInstance(DateFormat.MEDIUM, DateFormat.MEDIUM)

    override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): Row =
        Row(NavDebugEntryBinding.inflate(LayoutInflater.from(parent.context), parent, false))

    override fun onBindViewHolder(holder: Row, position: Int) {
        val entry = getItem(position)
        val binding = holder.binding
        val context = binding.root.context
        binding.historyTimestamp.text = timestamp.format(Date(entry.timeMillis))
        binding.historySummary.text = when (entry.kind) {
            NavDebugKind.READ -> describeCue(context, entry.cue)
            NavDebugKind.DISTANCE_ONLY -> context.getString(R.string.nav_debug_distance_only, entry.cue.distance)
            NavDebugKind.UNREADABLE -> context.getString(R.string.nav_debug_unreadable)
            NavDebugKind.FINAL_UNREADABLE -> listOf(
                context.getString(R.string.nav_debug_final_unreadable),
                describeCue(context, entry.cue),
            ).filter(String::isNotEmpty).joinToString(" · ")
            NavDebugKind.ENDED -> context.getString(R.string.nav_debug_ended)
        }
        val partial = entry.kind == NavDebugKind.UNREADABLE || entry.kind == NavDebugKind.DISTANCE_ONLY ||
            entry.kind == NavDebugKind.FINAL_UNREADABLE
        binding.historySources.isVisible = partial
        if (partial) {
            binding.historySources.text = if (entry.sources.isEmpty()) {
                context.getString(R.string.nav_debug_no_fields)
            } else {
                val fields = entry.sources.joinToString(", ") { it.name.lowercase(Locale.ROOT).replace('_', ' ') }
                context.getString(R.string.nav_debug_fields, fields)
            }
        }
    }

    private fun describeCue(context: Context, cue: NavState): String = buildList {
        if (cue.maneuver != "none" && !(cue.maneuver == "destination" && cue.arrival != "none")) {
            val turn = if (cue.maneuver == "uturn") context.getString(R.string.nav_debug_uturn)
                else cue.maneuver.replace('_', ' ').replaceFirstChar(Char::uppercaseChar)
            add(turn)
        }
        if (cue.exitNumber > 0) add(context.getString(R.string.nav_debug_exit, cue.exitNumber))
        when (cue.arrival) {
            "destination" -> add(context.getString(R.string.nav_debug_destination))
            "destination_left" -> add(context.getString(R.string.nav_debug_destination_left))
            "destination_right" -> add(context.getString(R.string.nav_debug_destination_right))
        }
        if (cue.distance.isNotEmpty()) add(cue.distance)
        else if (cue.maneuver != "none" && cue.maneuver != "destination") {
            add(context.getString(R.string.nav_debug_missing_distance))
        }
    }.joinToString(" · ")

    private object Diff : DiffUtil.ItemCallback<NavDebugEntry>() {
        override fun areItemsTheSame(old: NavDebugEntry, new: NavDebugEntry) = old == new
        override fun areContentsTheSame(old: NavDebugEntry, new: NavDebugEntry) = old == new
    }
}
