package io.github.rbotha78.webdisplay.manager

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.viewModels
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.lifecycleScope
import androidx.lifecycle.repeatOnLifecycle
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch

class MainActivity : ComponentActivity() {
    private val viewModel: ManagerViewModel by viewModels()
    private lateinit var discovery: NsdDiscovery

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        discovery = NsdDiscovery(this, viewModel::onDiscovered) { message ->
            viewModel.reportNotice(message)
        }
        setContent { ManagerScreen(viewModel) }
        lifecycleScope.launch {
            repeatOnLifecycle(Lifecycle.State.STARTED) {
                while (isActive) {
                    viewModel.refresh()
                    delay(20_000)
                }
            }
        }
    }

    override fun onStart() {
        super.onStart()
        PinnedHttp.resumeNetwork()
        discovery.start()
        viewModel.refresh()
    }

    override fun onStop() {
        discovery.stop()
        viewModel.cancelNetwork()
        super.onStop()
    }
}
