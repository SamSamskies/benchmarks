#!/usr/bin/env python3
"""Export saved single-model benchmark runs for the static dashboard."""

import argparse
import csv
from io import StringIO
import json
import math
from pathlib import Path
import re


def read_run(directory):
    metadata = json.loads((directory / 'metadata.json').read_text())
    log = (directory / 'run.log').read_text()
    csv_text = (directory / 'results.csv').read_text() if (directory / 'results.csv').is_file() else ''
    model = metadata['model']
    requested = metadata['benchmark']['runsRequested']
    if not isinstance(requested, int) or requested < 1:
        raise ValueError(f'{directory}: invalid requested run count')
    samples = []
    for match in re.finditer(r'^\s*run (\d+)/(\d+): (?:([\d.]+) tok/s \((\d+) tokens\)|(FAILED))\s*$', log, re.M):
        index, total, speed, tokens, failed = match.groups()
        if int(total) != requested:
            raise ValueError(f'{directory}: log run count differs from metadata')
        sample = {'index': int(index), 'status': 'failed' if failed else 'success', 'tokensPerSecond': None if failed else float(speed), 'tokens': None if failed else int(tokens)}
        if not 1 <= sample['index'] <= requested or any(s['index'] == sample['index'] for s in samples):
            raise ValueError(f'{directory}: duplicate or out-of-range sample')
        if not failed and (not math.isfinite(sample['tokensPerSecond']) or sample['tokensPerSecond'] <= 0 or sample['tokens'] <= 0):
            raise ValueError(f'{directory}: invalid successful sample')
        samples.append(sample)
    successful = [sample for sample in samples if sample['status'] == 'success']
    rows = list(csv.DictReader(StringIO(csv_text)))
    row = next((row for row in rows if row['model'] == model['name']), None)
    if row is None and successful:
        raise ValueError(f'{directory}: successful samples have no CSV summary')
    stats = None
    if row:
        stats = {'average': float(row['avg_tokens_per_sec']), 'min': float(row['min_tokens_per_sec']), 'max': float(row['max_tokens_per_sec'])}
        if int(row['runs']) != len(successful) or not successful:
            raise ValueError(f'{directory}: summary and log success counts differ')
        speeds = [sample['tokensPerSecond'] for sample in successful]
        if not all(math.isfinite(value) and value > 0 for value in stats.values()):
            raise ValueError(f'{directory}: invalid CSV summary')
        if abs(stats['average'] - sum(speeds) / len(speeds)) > 0.02 or abs(stats['min'] - min(speeds)) > 0.01 or abs(stats['max'] - max(speeds)) > 0.01:
            raise ValueError(f'{directory}: CSV summary does not match samples')
    loaded = metadata.get('loadedModelsAfter', {}).get('models', [])
    runtime = next((item for item in loaded if item['name'] == model['name']), {})
    return {
        'id': metadata['id'], 'startedAt': metadata['startedAt'], 'finishedAt': metadata.get('finishedAt'),
        'status': 'complete' if metadata.get('exitCode') == 0 and len(successful) == requested else 'partial',
        'exitCode': metadata.get('exitCode'),
        'model': {'name': model['name'], 'digest': model['digest'], 'sizeBytes': model.get('size'), 'runner': model.get('details', {}).get('runner'), 'quantization': model.get('details', {}).get('quantization_level')},
        'server': {'version': metadata['server']['version']}, 'hardware': metadata.get('hardware', {}),
        'configuration': metadata['benchmark'],
        'source': {'revision': metadata['source']['revision'], 'scriptSha256': metadata['source']['scriptSha256'], 'dirty': bool(metadata['source'].get('workingTree'))},
        'runtime': {'contextLength': runtime.get('context_length'), 'vramBytes': runtime.get('size_vram')},
        'samples': samples, 'stats': stats,
        'files': {'csv': csv_text, 'log': log, 'context': (directory / 'context.md').read_text()},
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('runs', type=Path, nargs='+', help='Saved run directories containing metadata.json, run.log, and results.csv')
    parser.add_argument('--output', type=Path, required=True, help='Dashboard dist directory')
    parser.add_argument('--standalone', type=Path, help='Also create a self-contained HTML dashboard at this path')
    args = parser.parse_args()
    try:
        runs = [read_run(directory) for directory in args.runs]
        if len({run['id'] for run in runs}) != len(runs):
            raise ValueError('Run IDs must be unique')
        dataset = {'schemaVersion': 1, 'runs': sorted(runs, key=lambda run: run['startedAt'])}
        encoded = json.dumps(dataset, indent=2, allow_nan=False)
        args.output.mkdir(parents=True, exist_ok=True)
        (args.output / 'benchmark.json').write_text(encoded + '\n')
        data_script = 'window.BENCHMARK_DATA = ' + encoded.replace('<', '\\u003c') + ';\n'
        (args.output / 'data.js').write_text(data_script)
        if args.standalone:
            if args.standalone.resolve() == (args.output / 'index.html').resolve():
                raise ValueError('Standalone output must differ from the source index.html')
            html = (args.output / 'index.html').read_text()
            styles = (args.output / 'styles.css').read_text()
            app_script = (args.output / 'app.js').read_text()
            html = html.replace('<link rel="stylesheet" href="styles.css">', '<style>' + styles + '</style>')
            html = html.replace('<script src="data.js" defer></script>', '').replace('<script src="app.js" defer></script>', '')
            scripts = data_script + '\n' + app_script
            scripts = re.sub(r'</script', lambda match: '<\\/script', scripts, flags=re.I)
            html = html.replace('</body>', '<script>\n' + scripts + '\n</script>\n</body>')
            args.standalone.parent.mkdir(parents=True, exist_ok=True)
            args.standalone.write_text(html)
            print(f'Standalone dashboard: {args.standalone}')
        print(f'Exported {len(runs)} run(s) to {args.output}')
    except (KeyError, ValueError, OSError) as error:
        parser.exit(1, f'error: {error}\n')


if __name__ == '__main__':
    main()
