package com.ex30companion

import android.content.pm.PackageManager
import android.os.Bundle
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import com.ex30companion.databinding.ActivityRationaleBinding

/**
 * One-shot consolidated permission prompt. Single screen, one button. Per
 * docs/permissions.md §"Runtime permission request flow": explain what we
 * read, that we never write, and that data only leaves the car over the
 * local Pi link. If the user denies, the app keeps working with whatever
 * was granted; we don't nag.
 */
class RationaleActivity : AppCompatActivity() {

    private lateinit var binding: ActivityRationaleBinding

    private val launcher = registerForActivityResult(
        ActivityResultContracts.RequestMultiplePermissions()
    ) { _ ->
        finish()
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityRationaleBinding.inflate(layoutInflater)
        setContentView(binding.root)

        binding.grant.setOnClickListener {
            val needed = PropertyCatalog.dangerousPermissions.filter {
                ContextCompat.checkSelfPermission(this, it) != PackageManager.PERMISSION_GRANTED
            }
            if (needed.isEmpty()) finish() else launcher.launch(needed.toTypedArray())
        }
        binding.skip.setOnClickListener { finish() }
    }
}
