"""Fail-closed validator for keywords present in the pinned V3 schema.

Not a general Draft7 implementation. Unknown keywords/formats are rejected,
so a workflow update cannot silently weaken validation. No third-party dependency.
"""
import re
from urllib.parse import urlparse


class SchemaError(ValueError):
    pass


SUPPORTED = {'$schema', 'title', 'description', 'default', 'type', 'required',
             'properties', 'pattern', 'format', 'enum', 'const', 'minLength',
             'maxLength', 'items', 'minItems', 'allOf', 'if', 'then', 'not'}


def check_schema(schema):
    unknown = set(schema) - SUPPORTED
    if unknown: raise RuntimeError(f'Unsupported schema keywords: {unknown}')
    if 'format' in schema and schema['format'] != 'uri':
        raise RuntimeError('Unsupported schema format')
    for sub in schema.get('properties', {}).values(): check_schema(sub)
    for sub in schema.get('allOf', []): check_schema(sub)
    for key in ['items', 'if', 'then', 'not']:
        if key in schema: check_schema(schema[key])


def validate(value, schema):
    def demand(ok, reason):
        if not ok: raise SchemaError(reason)
    if 'not' in schema:
        try: validate(value, schema['not'])
        except SchemaError: pass
        else: raise SchemaError('not constraint')
    if 'type' in schema:
        allowed = schema['type'] if isinstance(schema['type'], list) else [schema['type']]
        types = {'object': dict, 'array': list, 'string': str, 'boolean': bool, 'null': type(None)}
        demand(any(type(value) is types[t] for t in allowed), 'type')
    if 'enum' in schema: demand(value in schema['enum'], 'enum')
    if 'const' in schema: demand(type(value) is type(schema['const']) and value == schema['const'], 'const')
    if isinstance(value, str):
        if 'pattern' in schema: demand(re.search(schema['pattern'], value), 'pattern')
        if 'minLength' in schema: demand(len(value) >= schema['minLength'], 'minLength')
        if 'maxLength' in schema: demand(len(value) <= schema['maxLength'], 'maxLength')
        if schema.get('format') == 'uri': demand(urlparse(value).scheme and urlparse(value).netloc, 'uri')
    if isinstance(value, dict):
        demand(all(k in value for k in schema.get('required', [])), 'required')
        for key, sub in schema.get('properties', {}).items():
            if key in value: validate(value[key], sub)
    if isinstance(value, list):
        demand(len(value) >= schema.get('minItems', 0), 'minItems')
        if 'items' in schema:
            for v in value: validate(v, schema['items'])
    for sub in schema.get('allOf', []): validate(value, sub)
    if 'if' in schema:
        try: validate(value, schema['if'])
        except SchemaError: pass
        else: validate(value, schema.get('then', {}))
