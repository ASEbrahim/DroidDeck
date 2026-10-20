package com.droiddeck.launcher.gpu

import android.content.Context
import android.util.Log
import com.droiddeck.launcher.core.DeviceSupport
import com.droiddeck.launcher.core.FileUtils
import java.io.File

/**
 * The Vulkan that games see through the Mali bridge: the version, shaderInt64 and the push-constant
 * room, which is what decides the newest DXVK that can start on it (ComponentsManager.bridgeDxvk).
 *
 * Read from <bridge cache dir>/apps-vulkan, which a bridge server with Vulkan promotion writes after
 * its self-test (the version it offers, which can be above the driver's), as left by the last
 * session; else from the phone's own driver (VulkanInfo), which is what a bridge without promotion
 * offers. null for missing values: a rule must not guess from them.
 */
data class AppVulkan(
    val major: Int,
    val minor: Int,
    val shaderInt64: Boolean?,
    val maxPushConstants: Int?,
    val source: String,
) {
    fun atLeast(major: Int, minor: Int): Boolean = this.major > major || (this.major == major && this.minor >= minor)

    val label: String
        get() = "Vulkan $major.$minor, shaderInt64 ${shaderInt64?.let { if (it) "yes" else "no" } ?: "?"}, " +
            "push constants ${maxPushConstants ?: "?"} ($source)"

    companion object {
        private const val TAG = "AppVulkan"
        const val BRIDGE_FILE = "apps-vulkan"

        /** null when this session does not draw through the bridge, or nothing could be read. */
        fun detect(context: Context): AppVulkan? {
            if (!DeviceSupport.vkBridge(context)) return null
            val native = VulkanInfo.query()
            val file = File(VkBridgeComponent.cacheDir(context), BRIDGE_FILE)
            val fromBridge = FileUtils.readString(file)?.let { fromBridgeFile(it, native["device"]) }
            val found = fromBridge ?: fromDriver(native)
            Log.i(TAG, found?.label ?: "unknown (driver query: ${native["error"] ?: "no api line"})")
            return found
        }

        /**
         * The bridge's file: key=value lines (device, api, shader_int64 0/1, max_push_constants).
         * Ignored when it names another GPU than the driver's (a restored backup, a moved phone).
         */
        internal fun fromBridgeFile(text: String, driverDevice: String?): AppVulkan? {
            val map = keyValues(text)
            val device = map["device"]
            if (device != null && driverDevice != null && device != driverDevice) return null
            val (major, minor) = version(map["api"]) ?: return null
            return AppVulkan(major, minor, bool(map["shader_int64"]), map["max_push_constants"]?.toIntOrNull(), "bridge")
        }

        /** VulkanInfo's lines: api=1.1.177, shader_int64=yes|no, max_push_constants=128. */
        internal fun fromDriver(map: Map<String, String>): AppVulkan? {
            val (major, minor) = version(map["api"]) ?: return null
            return AppVulkan(major, minor, bool(map["shader_int64"]), map["max_push_constants"]?.toIntOrNull(), "driver")
        }

        private fun keyValues(text: String): Map<String, String> = text.lineSequence().mapNotNull { line ->
            val eq = line.indexOf('=')
            if (eq <= 0) null else line.substring(0, eq).trim() to line.substring(eq + 1).trim()
        }.toMap()

        private fun version(v: String?): Pair<Int, Int>? {
            val parts = v?.trim()?.split('.') ?: return null
            if (parts.size < 2) return null
            val major = parts[0].toIntOrNull() ?: return null
            val minor = parts[1].toIntOrNull() ?: return null
            return major to minor
        }

        private fun bool(v: String?): Boolean? = when (v?.trim()?.lowercase()) {
            "1", "yes", "true" -> true
            "0", "no", "false" -> false
            else -> null
        }
    }
}
