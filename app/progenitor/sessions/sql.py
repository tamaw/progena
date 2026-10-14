"""Postgres trigger that creates a session document when a user message is checkpointed.

Installed by ``open_langgraph_persistence`` right after ``saver.setup()``,
under the same advisory-lock pattern as ``CHECKPOINT_TRIGGER_SQL``.
"""

from app.progenitor.document_kinds import SESSION_KEY_PREFIX

# Only the SESSION_KEY_PREFIX constant is interpolated, never input.
SESSION_TRIGGER_SQL = f"""
SELECT pg_advisory_xact_lock(hashtext('progenitor_session_trigger'));

CREATE OR REPLACE FUNCTION progenitor_skip_value(blob bytea, pos int) RETURNS int AS $$
DECLARE
    len int := octet_length(blob);
    marker int;
    arr_len int;
    map_len int;
    i int;
    new_pos int;
BEGIN
    IF pos >= len THEN RETURN len; END IF;
    marker := get_byte(blob, pos);
    IF marker BETWEEN 0 AND 127 THEN RETURN pos + 1; END IF;
    IF marker BETWEEN 128 AND 143 THEN
        map_len := marker & 15;
        new_pos := pos + 1;
        FOR i IN 1..map_len LOOP
            new_pos := progenitor_skip_value(blob, new_pos);
            new_pos := progenitor_skip_value(blob, new_pos);
        END LOOP;
        RETURN new_pos;
    END IF;
    IF marker BETWEEN 144 AND 159 THEN
        arr_len := marker & 15;
        new_pos := pos + 1;
        FOR i IN 1..arr_len LOOP
            new_pos := progenitor_skip_value(blob, new_pos);
        END LOOP;
        RETURN new_pos;
    END IF;
    IF marker BETWEEN 160 AND 191 THEN RETURN pos + 1 + (marker & 31); END IF;
    IF marker BETWEEN 224 AND 255 THEN RETURN pos + 1; END IF;
    IF marker = 192 OR marker = 194 OR marker = 195 THEN RETURN pos + 1; END IF;
    IF marker = 202 THEN RETURN pos + 5; END IF;
    IF marker = 203 THEN RETURN pos + 9; END IF;
    IF marker = 204 THEN RETURN pos + 2; END IF;
    IF marker = 205 THEN RETURN pos + 3; END IF;
    IF marker = 206 THEN RETURN pos + 5; END IF;
    IF marker = 207 THEN RETURN pos + 9; END IF;
    IF marker = 208 THEN RETURN pos + 2; END IF;
    IF marker = 209 THEN RETURN pos + 3; END IF;
    IF marker = 210 THEN RETURN pos + 5; END IF;
    IF marker = 211 THEN RETURN pos + 9; END IF;
    IF marker = 212 THEN RETURN pos + 3; END IF;
    IF marker = 213 THEN RETURN pos + 4; END IF;
    IF marker = 214 THEN RETURN pos + 5; END IF;
    IF marker = 215 THEN RETURN pos + 9; END IF;
    IF marker = 216 THEN RETURN pos + 17; END IF;
    IF marker = 217 THEN RETURN pos + 2 + get_byte(blob, pos+1); END IF;
    IF marker = 218 THEN RETURN pos + 3 + ((get_byte(blob, pos+1) << 8) | get_byte(blob, pos+2)); END IF;
    IF marker = 219 THEN RETURN pos + 5 + ((get_byte(blob, pos+1) << 24) | (get_byte(blob, pos+2) << 16) | (get_byte(blob, pos+3) << 8) | get_byte(blob, pos+4)); END IF;
    IF marker = 220 THEN
        arr_len := (get_byte(blob, pos+1) << 8) | get_byte(blob, pos+2);
        new_pos := pos + 3;
        FOR i IN 1..arr_len LOOP
            new_pos := progenitor_skip_value(blob, new_pos);
        END LOOP;
        RETURN new_pos;
    END IF;
    IF marker = 221 THEN
        arr_len := (get_byte(blob, pos+1) << 24) | (get_byte(blob, pos+2) << 16) | (get_byte(blob, pos+3) << 8) | get_byte(blob, pos+4);
        new_pos := pos + 5;
        FOR i IN 1..arr_len LOOP
            new_pos := progenitor_skip_value(blob, new_pos);
        END LOOP;
        RETURN new_pos;
    END IF;
    IF marker = 222 THEN
        map_len := (get_byte(blob, pos+1) << 8) | get_byte(blob, pos+2);
        new_pos := pos + 3;
        FOR i IN 1..map_len LOOP
            new_pos := progenitor_skip_value(blob, new_pos);
            new_pos := progenitor_skip_value(blob, new_pos);
        END LOOP;
        RETURN new_pos;
    END IF;
    IF marker = 223 THEN
        map_len := (get_byte(blob, pos+1) << 24) | (get_byte(blob, pos+2) << 16) | (get_byte(blob, pos+3) << 8) | get_byte(blob, pos+4);
        new_pos := pos + 5;
        FOR i IN 1..map_len LOOP
            new_pos := progenitor_skip_value(blob, new_pos);
            new_pos := progenitor_skip_value(blob, new_pos);
        END LOOP;
        RETURN new_pos;
    END IF;
    IF marker = 199 THEN RETURN pos + 3 + get_byte(blob, pos+1); END IF;
    IF marker = 200 THEN RETURN pos + 4 + ((get_byte(blob, pos+1) << 8) | get_byte(blob, pos+2)); END IF;
    IF marker = 201 THEN RETURN pos + 6 + ((get_byte(blob, pos+1) << 24) | (get_byte(blob, pos+2) << 16) | (get_byte(blob, pos+3) << 8) | get_byte(blob, pos+4)); END IF;
    RETURN len;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION progenitor_human_text(blob bytea) RETURNS text AS $$
DECLARE
    pos int := 0;
    len int := octet_length(blob);
    marker int;
    str_len int;
    content_pos int;
    arr_len int;
    i int;
    j int;
    kmarker int;
    klen int;
    vmarker int;
    elem_marker int;
    elem_len int;
    elem_pos int;
    key_text text;
BEGIN
    WHILE pos <= len - 30 LOOP
        IF get_byte(blob, pos) = 189 THEN
            IF substring(blob from pos+2 for 29) = '\\x6c616e67636861696e5f636f72652e6d657373616765732e68756d616e'::bytea THEN
                EXIT;
            END IF;
        END IF;
        pos := pos + 1;
    END LOOP;
    IF pos > len - 30 THEN
        RETURN NULL;
    END IF;
    pos := pos + 30;
    WHILE pos <= len - 8 LOOP
        IF get_byte(blob, pos) = 167 THEN
            IF substring(blob from pos+2 for 7) = '\\x636f6e74656e74'::bytea THEN
                EXIT;
            END IF;
        END IF;
        pos := pos + 1;
    END LOOP;
    IF pos > len - 8 THEN
        RETURN NULL;
    END IF;
    pos := pos + 8;
    IF pos >= len THEN RETURN NULL; END IF;
    marker := get_byte(blob, pos);
    IF marker BETWEEN 160 AND 191 THEN
        str_len := marker & 31;
        RETURN convert_from(substring(blob from pos+2 for str_len), 'UTF8');
    ELSIF marker = 217 THEN
        str_len := get_byte(blob, pos+1);
        RETURN convert_from(substring(blob from pos+3 for str_len), 'UTF8');
    ELSIF marker = 218 THEN
        str_len := (get_byte(blob, pos+1) << 8) | get_byte(blob, pos+2);
        RETURN convert_from(substring(blob from pos+4 for str_len), 'UTF8');
    ELSIF marker = 219 THEN
        str_len := (get_byte(blob, pos+1) << 24) | (get_byte(blob, pos+2) << 16) | (get_byte(blob, pos+3) << 8) | get_byte(blob, pos+4);
        RETURN convert_from(substring(blob from pos+6 for str_len), 'UTF8');
    ELSIF marker BETWEEN 144 AND 159 THEN
        arr_len := marker & 15;
        content_pos := pos + 1;
    ELSIF marker = 220 THEN
        arr_len := (get_byte(blob, pos+1) << 8) | get_byte(blob, pos+2);
        content_pos := pos + 3;
    ELSIF marker = 221 THEN
        arr_len := (get_byte(blob, pos+1) << 24) | (get_byte(blob, pos+2) << 16) | (get_byte(blob, pos+3) << 8) | get_byte(blob, pos+4);
        content_pos := pos + 5;
    ELSE
        RETURN NULL;
    END IF;
    pos := content_pos;
    FOR i IN 1..arr_len LOOP
        IF pos >= len THEN RETURN ''; END IF;
        elem_marker := get_byte(blob, pos);
        IF NOT (elem_marker BETWEEN 128 AND 143) THEN
            RETURN '';
        END IF;
        elem_len := elem_marker & 15;
        elem_pos := pos + 1;
        FOR j IN 1..elem_len LOOP
            kmarker := get_byte(blob, elem_pos);
            IF kmarker BETWEEN 160 AND 191 THEN
                klen := kmarker & 31;
                key_text := convert_from(substring(blob from elem_pos+2 for klen), 'UTF8');
                elem_pos := elem_pos + 1 + klen;
            ELSIF kmarker = 217 THEN
                klen := get_byte(blob, elem_pos+1);
                key_text := convert_from(substring(blob from elem_pos+3 for klen), 'UTF8');
                elem_pos := elem_pos + 2 + klen;
            ELSIF kmarker = 218 THEN
                klen := (get_byte(blob, elem_pos+1) << 8) | get_byte(blob, elem_pos+2);
                key_text := convert_from(substring(blob from elem_pos+4 for klen), 'UTF8');
                elem_pos := elem_pos + 3 + klen;
            ELSIF kmarker = 219 THEN
                klen := (get_byte(blob, elem_pos+1) << 24) | (get_byte(blob, elem_pos+2) << 16) | (get_byte(blob, elem_pos+3) << 8) | get_byte(blob, elem_pos+4);
                key_text := convert_from(substring(blob from elem_pos+6 for klen), 'UTF8');
                elem_pos := elem_pos + 5 + klen;
            ELSE
                RETURN '';
            END IF;
            IF key_text = 'text' THEN
                vmarker := get_byte(blob, elem_pos);
                IF vmarker BETWEEN 160 AND 191 THEN
                    RETURN convert_from(substring(blob from elem_pos+2 for (vmarker & 31)), 'UTF8');
                ELSIF vmarker = 217 THEN
                    RETURN convert_from(substring(blob from elem_pos+3 for get_byte(blob, elem_pos+1)), 'UTF8');
                ELSIF vmarker = 218 THEN
                    RETURN convert_from(substring(blob from elem_pos+4 for ((get_byte(blob, elem_pos+1) << 8) | get_byte(blob, elem_pos+2))), 'UTF8');
                ELSIF vmarker = 219 THEN
                    RETURN convert_from(substring(blob from elem_pos+6 for ((get_byte(blob, elem_pos+1) << 24) | (get_byte(blob, elem_pos+2) << 16) | (get_byte(blob, elem_pos+3) << 8) | get_byte(blob, elem_pos+4))), 'UTF8');
                ELSE
                    RETURN '';
                END IF;
            ELSE
                elem_pos := progenitor_skip_value(blob, elem_pos);
            END IF;
        END LOOP;
        pos := elem_pos;
    END LOOP;
    RETURN '';
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION progenitor_create_session()
RETURNS trigger AS $$
DECLARE
    _thread_id text;
    _source text;
    _text text;
    _metadata jsonb;
    _now timestamptz := now();
    _stamp text := to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"+00:00"');
BEGIN
    SELECT c.thread_id, c.metadata->>'progenitor_source', c.metadata
    INTO _thread_id, _source, _metadata
    FROM checkpoints c
    WHERE c.thread_id = NEW.thread_id
      AND c.checkpoint_ns = NEW.checkpoint_ns
      AND c.checkpoint_id = NEW.checkpoint_id;

    IF _metadata IS NULL OR _metadata->>'source' != 'input' THEN
        RETURN NEW;
    END IF;
    IF _source IS NULL OR _source NOT IN ('ui', 'telegram', 'api') THEN
        RETURN NEW;
    END IF;

    _text := progenitor_human_text(NEW.blob);
    IF _text IS NULL THEN
        RETURN NEW;
    END IF;

    IF _text = '' THEN
        _text := '* sends an image';
    END IF;
    INSERT INTO store (prefix, key, value, created_at, updated_at)
    VALUES (
        'progenitor.state',
        '{SESSION_KEY_PREFIX}' || _thread_id,
        jsonb_build_object('payload', jsonb_build_object(
            'id', _thread_id,
            'thread_id', _thread_id,
            'source', _source,
            'title', 'New chat',
            'first_message', LEFT(_text, 4000),
            'pinned', false,
            'archived', false,
            'created_at', _stamp,
            'last_activity', _stamp,
            'content', ''
        )),
        _now,
        _now
    )
    ON CONFLICT (prefix, key) DO NOTHING;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS progenitor_create_session ON checkpoint_writes;
CREATE TRIGGER progenitor_create_session
AFTER INSERT ON checkpoint_writes
FOR EACH ROW
WHEN (NEW.checkpoint_ns = '' AND NEW.channel = 'messages')
EXECUTE FUNCTION progenitor_create_session();
"""  # nosec B608
