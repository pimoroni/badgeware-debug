// Memory inspection: the GC heap's layout, and raw reads of RAM and flash, so a
// host can map the heap from its allocation table without the badge walking
// it, as gc.mem_free() does.

#include <stdint.h>
#include <string.h>

#include "py/runtime.h"
#include "py/mperrno.h"
#include "py/mpstate.h"
#include "pico.h"
#include "hardware/regs/addressmap.h"
#include "cdcaux.h"

#if MICROPY_HW_USB_CDC_AUX

#if MICROPY_GC_SPLIT_HEAP
#define MEM_NEXT_AREA(area) ((area)->next)
#else
#define MEM_NEXT_AREA(area) (NULL)
#endif

// SRAM, the flash window and the heap areas (which may be in PSRAM) can be
// read. Anything else could fault, so it is refused.
static bool mem_readable(uintptr_t address, size_t length) {
    uintptr_t end = address + length;
    if (end < address) {
        return false;
    }
    if (address >= SRAM_BASE && end <= SRAM_END) {
        return true;
    }
    if (address >= XIP_BASE && end <= XIP_BASE + PICO_FLASH_SIZE_BYTES) {
        return true;
    }
    for (const mp_state_mem_area_t *area = &MP_STATE_MEM(area); area != NULL; area = MEM_NEXT_AREA(area)) {
        // An area's tables come first, then its pool.
        if (address >= (uintptr_t)area->gc_alloc_table_start && end <= (uintptr_t)area->gc_pool_end) {
            return true;
        }
    }
    return false;
}

static void mem_heap(mp_obj_t id) {
    cdcaux_reply_t reply;
    cdcaux_reply_begin(&reply, "mem.heap", id);
    mp_printf(&reply.print, ", \"block\": %u, \"areas\": [", (unsigned int)MICROPY_BYTES_PER_GC_BLOCK);
    bool first = true;
    for (const mp_state_mem_area_t *area = &MP_STATE_MEM(area); area != NULL; area = MEM_NEXT_AREA(area)) {
        mp_printf(&reply.print, "%s{\"table\": %u, \"table_bytes\": %u, \"pool\": %u, \"pool_bytes\": %u, \"last_used_block\": %u}",
            first ? "" : ", ",
            (unsigned int)(uintptr_t)area->gc_alloc_table_start, (unsigned int)area->gc_alloc_table_byte_len,
            (unsigned int)(uintptr_t)area->gc_pool_start, (unsigned int)(area->gc_pool_end - area->gc_pool_start),
            (unsigned int)area->gc_last_used_block);
        first = false;
    }
    mp_printf(&reply.print, "]");
    cdcaux_reply_send(&reply, NULL, 0);
}

static void mem_read(mp_obj_t request, mp_obj_t id) {
    mp_int_t address = cdcaux_get_int(request, MP_QSTR_address, -1);
    mp_int_t length = cdcaux_get_int(request, MP_QSTR_length, 0);
    if (address < 0 || length < 0 || length > CDCAUX_CHUNK || !mem_readable((uintptr_t)address, (size_t)length)) {
        mp_raise_OSError(MP_EINVAL);
    }
    cdcaux_reply_t reply;
    cdcaux_reply_begin(&reply, "mem.read", id);
    // Sent straight from memory: what the host gets is a snapshot as it was sent.
    cdcaux_reply_send(&reply, (const uint8_t *)(uintptr_t)address, (size_t)length);
}

static bool mem_dispatch(const char *cmd, mp_obj_t request, mp_obj_t id, const uint8_t *payload, size_t payload_len) {
    (void)payload;
    (void)payload_len;
    if (strcmp(cmd, "mem.heap") == 0) {
        mem_heap(id);
    } else if (strcmp(cmd, "mem.read") == 0) {
        mem_read(request, id);
    } else {
        return false;
    }
    return true;
}

static void mem_service(bool connected) {
    (void)connected;
}

static void mem_nothing(void) {
}

const cdcaux_role_t cdcaux_mem_role = {
    .prefix = "mem.",
    .dispatch = mem_dispatch,
    .service = mem_service,
    .disconnected = mem_nothing,
    .soft_reset = mem_nothing,
};

#endif
