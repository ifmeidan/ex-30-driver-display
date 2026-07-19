package com.ex30companion

import android.view.LayoutInflater
import android.view.ViewGroup
import androidx.recyclerview.widget.DiffUtil
import androidx.recyclerview.widget.ListAdapter
import androidx.recyclerview.widget.RecyclerView
import com.ex30companion.databinding.ItemPropertyBinding

data class PropertyRow(
    val entry: PropertyCatalog.Entry,
    val sample: PropertySample,
    val nowMs: Long,
)

class PropertyListAdapter :
    ListAdapter<PropertyRow, PropertyListAdapter.VH>(DIFF) {

    override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): VH {
        val inf = LayoutInflater.from(parent.context)
        return VH(ItemPropertyBinding.inflate(inf, parent, false))
    }

    override fun onBindViewHolder(holder: VH, position: Int) = holder.bind(getItem(position))

    class VH(private val b: ItemPropertyBinding) : RecyclerView.ViewHolder(b.root) {
        fun bind(row: PropertyRow) {
            val ctx = b.root.context
            b.name.text = row.entry.name
            val s = row.sample
            b.value.text = when (s.status) {
                PropertyStatus.UNKNOWN -> "—"
                PropertyStatus.OK -> formatValue(s.value)
                PropertyStatus.NOT_SUPPORTED -> "not supported"
                PropertyStatus.PERMISSION_DENIED -> "permission denied"
                PropertyStatus.ERROR -> "error"
            }
            val ageMs = if (s.timestampMs == 0L) -1L else row.nowMs - s.timestampMs
            b.age.text = when {
                s.status == PropertyStatus.UNKNOWN -> "never"
                ageMs < 0 -> "never"
                ageMs < 1000 -> "${ageMs}ms"
                ageMs < 60_000 -> "${ageMs / 1000}s"
                else -> "${ageMs / 60_000}m"
            }
            b.tier.text = row.entry.tier.name.lowercase()
            b.detail.text = s.detail.orEmpty()
            b.detail.visibility = if (s.detail.isNullOrEmpty()) android.view.View.GONE else android.view.View.VISIBLE

            val colorRes = when {
                s.status == PropertyStatus.UNKNOWN -> R.color.staleness_never
                s.status != PropertyStatus.OK -> R.color.staleness_error
                ageMs < 1000 -> R.color.staleness_fresh
                ageMs < 5000 -> R.color.staleness_warm
                else -> R.color.staleness_stale
            }
            b.staleness.setBackgroundColor(ctx.getColor(colorRes))
        }

        private fun formatValue(v: Any?): String = when (v) {
            null -> "null"
            is FloatArray -> v.joinToString(prefix = "[", postfix = "]") { "%.3f".format(it) }
            is IntArray -> v.joinToString(prefix = "[", postfix = "]")
            is LongArray -> v.joinToString(prefix = "[", postfix = "]")
            is Float -> "%.3f".format(v)
            is Double -> "%.3f".format(v)
            else -> v.toString()
        }
    }

    companion object {
        private val DIFF = object : DiffUtil.ItemCallback<PropertyRow>() {
            override fun areItemsTheSame(a: PropertyRow, b: PropertyRow) =
                a.entry.propertyId == b.entry.propertyId

            override fun areContentsTheSame(a: PropertyRow, b: PropertyRow) =
                a.sample == b.sample && a.nowMs / 250 == b.nowMs / 250
        }
    }
}
