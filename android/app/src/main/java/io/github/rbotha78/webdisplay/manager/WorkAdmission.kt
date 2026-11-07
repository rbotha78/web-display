package io.github.rbotha78.webdisplay.manager

internal class WorkAdmission {
    enum class Result { START, COALESCED, REJECTED }
    private val active = mutableSetOf<String>()
    private val pending = mutableSetOf<String>()

    @Synchronized
    fun admit(key: String, coalesce: Boolean): Result {
        if (active.add(key)) return Result.START
        if (!coalesce) return Result.REJECTED
        pending.add(key)
        return Result.COALESCED
    }

    @Synchronized
    fun takeRepeat(key: String): Boolean = pending.remove(key)

    @Synchronized
    fun cancel(key: String) {
        pending.remove(key)
        active.remove(key)
    }
}

internal class ResolveQueue<T> {
    data class Ticket<T>(val key: String, val value: T, val generation: Int, var cancelled: Boolean = false)
    private val pending = linkedMapOf<String, Ticket<T>>()
    private var active: Ticket<T>? = null
    private var generation = 0
    private var running = false

    fun start() {
        generation++
        running = true
    }

    fun stop() {
        generation++
        running = false
        pending.clear()
        active?.cancelled = true
    }

    fun enqueue(key: String, value: T) {
        if (!running || pending.containsKey(key)) return
        if (active?.let { it.key == key && isCurrent(it) } == true) return
        pending[key] = Ticket(key, value, generation)
    }

    fun remove(key: String) {
        pending.remove(key)
        active?.takeIf { it.key == key }?.cancelled = true
    }

    fun next(): Ticket<T>? {
        if (active != null || !running) return null
        val next = pending.entries.firstOrNull() ?: return null
        pending.remove(next.key)
        return next.value.also { active = it }
    }

    fun isCurrent(ticket: Ticket<T>): Boolean =
        running && !ticket.cancelled && ticket.generation == generation

    fun complete(ticket: Ticket<T>) {
        if (active === ticket) active = null
    }
}
