from pathlib import Path
import yaml

DEFAULT_REGISTRY = Path(__file__).resolve().parents[1] / 'data' / 'features.yaml'


class Registry:
    def __init__(self, path=DEFAULT_REGISTRY):
        content = yaml.safe_load(Path(path).read_text(encoding='utf-8'))
        features = content.get('features', [])
        self.items = {item['key']: item for item in features}
        if len(features) != len(self.items):
            raise ValueError('Duplicate feature key')
        for item in features:
            if not {'key', 'data_type', 'scope', 'strategy', 'version'} <= item.keys():
                raise ValueError(f'Incomplete feature: {item}')

    def get(self, key):
        if key not in self.items:
            raise KeyError(key)
        return self.items[key]

    def validate(self, key, value):
        kind = self.get(key)['data_type']
        checks = {
            'boolean': lambda v: type(v) is bool,
            'integer': lambda v: type(v) is int,
            'number': lambda v: isinstance(v, (int, float)) and type(v) is not bool,
            'string': lambda v: isinstance(v, str),
            'object': lambda v: isinstance(v, dict),
        }
        if kind not in checks or not checks[kind](value):
            raise ValueError(f'Invalid {kind} for {key}')
        return True

    def public(self):
        return list(self.items.values())
