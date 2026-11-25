// cdcaux.describe(obj): what the debugger shows for a Python function, which
// MicroPython keeps in C structures Python can't reach on this port: where it
// was defined, its parameters and their defaults, a closure's captured
// variables and a bound method's self.

#include "py/runtime.h"
#include "py/bc.h"
#include "py/emitglue.h"
#include "py/objfun.h"
#include "py/profile.h"
#include "cdcaux.h"

#if MICROPY_HW_USB_CDC_AUX && MICROPY_PY_SYS_SETTRACE && MICROPY_PY_SYS_SETTRACE_LOCALNAMES

static mp_obj_t describe_name(const mp_raw_code_t *rc, size_t local_num) {
    qstr name = mp_raw_code_get_local_name(rc, local_num);
    return name == MP_QSTRnull ? mp_const_none : MP_OBJ_NEW_QSTR(name);
}

static void describe_store(mp_obj_t dict, qstr key, mp_obj_t value) {
    mp_obj_dict_store(dict, MP_OBJ_NEW_QSTR(key), value);
}

// Returns a dict, or None for anything that isn't a bytecode function.
static mp_obj_t cdcaux_describe(mp_obj_t obj) {
    mp_obj_t bound_self = MP_OBJ_NULL;
    if (mp_obj_is_type(obj, &mp_type_bound_meth)) {
        obj = mp_obj_bound_meth_get(obj, &bound_self);
    }
    size_t n_closed = 0;
    const mp_obj_t *closed = NULL;
    if (mp_obj_is_type(obj, &mp_type_closure)) {
        obj = mp_obj_closure_get(obj, &n_closed, &closed);
    }
    if (!mp_obj_is_type(obj, &mp_type_fun_bc) && !mp_obj_is_type(obj, &mp_type_gen_wrap)) {
        return mp_const_none;
    }

    const mp_obj_fun_bc_t *fun = MP_OBJ_TO_PTR(obj);
    const mp_raw_code_t *rc = fun->rc;
    const mp_bytecode_prelude_t *prelude = &rc->prelude;
    mp_obj_t info = mp_obj_new_dict(0);
    describe_store(info, MP_QSTR_name, MP_OBJ_NEW_QSTR(fun->context->constants.qstr_table[prelude->qstr_block_name_idx]));
    describe_store(info, MP_QSTR_file, MP_OBJ_NEW_QSTR(fun->context->constants.qstr_table[0]));
    // The def line, or the body's first line where that wasn't recorded, as
    // for code loaded from a .mpy file.
    size_t line = rc->line_of_definition ? rc->line_of_definition : mp_prof_bytecode_lineno(rc, 0);
    describe_store(info, MP_QSTR_line, MP_OBJ_NEW_SMALL_INT(line));
    describe_store(info, MP_QSTR_generator, mp_obj_new_bool(prelude->scope_flags & MP_SCOPE_FLAG_GENERATOR));

    // A closure's captured variables are passed as its first arguments, so
    // its own parameters follow them. Positional defaults belong to the last
    // parameters, and keyword defaults follow them as a dict.
    size_t n_pos = prelude->n_pos_args;
    size_t n_kwonly = prelude->n_kwonly_args;
    size_t n_def = prelude->n_def_pos_args;
    mp_obj_t params = mp_obj_new_list(0, NULL);
    for (size_t i = n_closed; i < n_pos; i++) {
        mp_obj_t entry[2] = { describe_name(rc, i), MP_OBJ_NULL };
        size_t n = 1;
        if (i >= n_pos - n_def) {
            entry[1] = fun->extra_args[i - (n_pos - n_def)];
            n = 2;
        }
        mp_obj_list_append(params, mp_obj_new_tuple(n, entry));
    }
    describe_store(info, MP_QSTR_params, params);

    mp_obj_t kw_defaults = (prelude->scope_flags & MP_SCOPE_FLAG_DEFKWARGS) ? fun->extra_args[n_def] : MP_OBJ_NULL;
    mp_obj_t kwonly = mp_obj_new_list(0, NULL);
    for (size_t i = n_pos; i < n_pos + n_kwonly; i++) {
        mp_obj_t entry[2] = { describe_name(rc, i), MP_OBJ_NULL };
        size_t n = 1;
        if (kw_defaults != MP_OBJ_NULL && entry[0] != mp_const_none) {
            mp_map_elem_t *elem = mp_map_lookup(mp_obj_dict_get_map(kw_defaults), entry[0], MP_MAP_LOOKUP);
            if (elem != NULL) {
                entry[1] = elem->value;
                n = 2;
            }
        }
        mp_obj_list_append(kwonly, mp_obj_new_tuple(n, entry));
    }
    describe_store(info, MP_QSTR_kwonly, kwonly);

    size_t next = n_pos + n_kwonly;
    if (prelude->scope_flags & MP_SCOPE_FLAG_VARARGS) {
        describe_store(info, MP_QSTR_varargs, describe_name(rc, next++));
    }
    if (prelude->scope_flags & MP_SCOPE_FLAG_VARKEYWORDS) {
        describe_store(info, MP_QSTR_varkw, describe_name(rc, next));
    }

    mp_obj_t captured = mp_obj_new_list(0, NULL);
    for (size_t i = 0; i < n_closed; i++) {
        mp_obj_t value = mp_obj_is_type(closed[i], &mp_type_cell) ? mp_obj_cell_get(closed[i]) : closed[i];
        mp_obj_t entry[2] = { describe_name(rc, i), value == MP_OBJ_NULL ? mp_const_none : value };
        mp_obj_list_append(captured, mp_obj_new_tuple(2, entry));
    }
    describe_store(info, MP_QSTR_captured, captured);

    if (bound_self != MP_OBJ_NULL) {
        describe_store(info, MP_QSTR_self, bound_self);
    }
    return info;
}

#else

static mp_obj_t cdcaux_describe(mp_obj_t obj) {
    (void)obj;
    return mp_const_none;
}

#endif

MP_DEFINE_CONST_FUN_OBJ_1(cdcaux_describe_obj, cdcaux_describe);
