#!/usr/bin/env python3
"""Build an offline HTML deck from JSON and the approved Emmanuil assets."""
import argparse
import base64
import html
import json
import mimetypes
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / 'assets'


def data_uri(path):
    path = Path(path)
    if not path.is_file() or path.stat().st_size == 0:
        raise ValueError(f'Отсутствует или пустой ресурс: {path}')
    mime = mimetypes.guess_type(path.name)[0] or 'application/octet-stream'
    return f'data:{mime};base64,' + base64.b64encode(path.read_bytes()).decode()


def esc(value):
    return html.escape(str(value), quote=True).replace('\n', '<br>')


def validate(deck):
    if not isinstance(deck, dict) or not isinstance(deck.get('slides'), list) or not deck['slides']:
        raise ValueError('Нужен объект с непустым массивом slides')
    for key in ('title', 'speaker'):
        if not isinstance(deck.get(key), str) or not deck[key].strip():
            raise ValueError(f'Нужно заполнить {key}; не придумывай данные спикера')
    required = {
        'cover': ('title',), 'definition': ('title', 'text'),
        'rows': ('title', 'items'), 'columns': ('title', 'items'),
        'quote': ('title', 'text', 'reference'), 'closing': ('title',),
    }
    for index, slide in enumerate(deck['slides'], 1):
        if not isinstance(slide, dict) or slide.get('kind') not in required:
            raise ValueError(f'Слайд {index}: неизвестный kind')
        for key in required[slide['kind']]:
            if key not in slide or not slide[key]:
                raise ValueError(f'Слайд {index}: нужно заполнить {key}')
        if slide['kind'] in ('rows', 'columns'):
            if not isinstance(slide['items'], list) or not 1 <= len(slide['items']) <= 3:
                raise ValueError(f'Слайд {index}: items должен содержать 1–3 пункта; раздели длинный слайд')
            for item in slide['items']:
                if not isinstance(item, dict) or not item.get('title') or not item.get('text'):
                    raise ValueError(f'Слайд {index}: каждому пункту нужны title и text')


def build(deck_path, output):
    deck_path = Path(deck_path).resolve()
    deck = json.loads(deck_path.read_text())
    validate(deck)
    background = data_uri(ASSETS / 'brand/background.png')
    mark = data_uri(ASSETS / 'brand/mark.svg')
    inner = data_uri(ASSETS / 'brand/mark-in.svg')
    word = data_uri(ASSETS / 'brand/word.svg')
    fonts = (ASSETS / 'fonts.css').read_text()
    fonts = re.sub(r'url\(([\"\']?)(fonts/[^)\"\']+)\1\)',
                   lambda m: 'url("' + data_uri(ASSETS / m[2]) + '")', fonts)
    css = (ASSETS / 'theme.css').read_text()
    brand = (f'<div class="brand"><span><img class="outer-mark" src="{mark}" alt="">'
             f'<img class="inner-mark" src="{inner}" alt=""></span>'
             f'<img class="word" src="{word}" alt="Эммануил"></div>')
    sections, manifest = [], []
    for index, slide in enumerate(deck['slides'], 1):
        kind = slide['kind']
        title = esc(slide['title'])
        eyebrow = esc(slide.get('eyebrow', ''))
        subtitle = esc(slide.get('subtitle', ''))
        image_path = None
        if slide.get('image'):
            candidate = Path(slide['image'])
            if candidate.is_absolute():
                image_path = candidate
            elif (deck_path.parent / candidate).is_file():
                image_path = deck_path.parent / candidate
            else:
                image_path = ASSETS / candidate
            image_uri = data_uri(image_path)
        figure = f'<figure><img src="{image_uri}" alt="{esc(slide.get("image_alt", ""))}"></figure>' if image_path else ''
        if kind == 'cover':
            content = (f'<div class="cover"><p class="eyebrow">{esc(deck["speaker"].upper())}</p>'
                       f'<h1>{title}' + (f'<br><em>{subtitle}</em>' if subtitle else '') +
                       '</h1><div class="rule"></div></div>')
            classes = 'cover-slide'
        elif kind == 'definition':
            content = f'<p class="eyebrow">{eyebrow}</p><h2>{title}</h2><p class="definition">{esc(slide["text"])}</p>'
            classes = 'definition-slide'
        elif kind == 'rows':
            rows = ''.join(f'<div class="row"><span class="num">{i:02}</span><h3>{esc(v["title"])}</h3><p>{esc(v["text"])}</p></div>'
                           for i, v in enumerate(slide['items'], 1))
            content, classes = f'<h2>{title}</h2><div class="rows">{rows}</div>', ''
        elif kind == 'columns':
            columns = ''.join(f'<div><span class="num">{i:02}</span><h3>{esc(v["title"])}</h3><p>{esc(v["text"])}</p></div>'
                              for i, v in enumerate(slide['items'], 1))
            content = f'<h2>{title}</h2><p class="eyebrow below">{eyebrow}</p><div class="columns">{columns}</div>'
            if slide.get('scope_text'):
                content += f'<div class="sphere"><span>{esc(slide.get("scope_title", ""))}</span><p>{esc(slide["scope_text"])}</p></div>'
            classes = 'types'
        elif kind == 'quote':
            context = f'<p class="context">{esc(slide["context"])}</p>' if slide.get('context') else ''
            content = (f'<div class="copy"><h2>{title}</h2>{context}'
                       f'<blockquote>«{esc(slide["text"])}»</blockquote>'
                       f'<p class="ref">{esc(slide["reference"])}</p></div>{figure}')
            classes = ('illustrated' if image_path else 'quotation') + (' dense' if slide.get('dense') else '')
        else:
            content = (f'<div class="copy"><p class="eyebrow">{eyebrow}</p><h2 class="final-words">{title}' +
                       (f'<br><em>{subtitle}</em>' if subtitle else '') + f'</h2></div>{figure}')
            classes = 'illustrated closing' if image_path else 'closing quotation'
        sections.append(f'<section class="slide {classes}"><img class="texture" src="{background}" alt="">'
                        f'<header>{brand}<span>{esc(slide.get("chapter", ""))}</span></header>'
                        f'<main>{content}</main><footer><span>{esc(deck["speaker"].upper())}</span>'
                        f'<span>{index:02} / {len(deck["slides"]):02}</span></footer></section>')
        manifest.append({'slide': index, 'kind': kind, 'title': slide['title'],
                         'reference': slide.get('reference'), 'quote': slide.get('text') if kind == 'quote' else None,
                         'image': str(image_path) if image_path else None})
    result = ('<!doctype html><html lang="ru"><head><meta charset="utf-8">'
              f'<title>{esc(deck["title"])} · {esc(deck["speaker"])}</title>'
              f'<style>{fonts}\n{css}</style></head><body>' + ''.join(sections) + '</body></html>')
    output = Path(output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(result)
    manifest_path = output.with_suffix('.manifest.json')
    manifest_path.write_text(json.dumps({'speaker': deck['speaker'], 'slides': manifest}, ensure_ascii=False, indent=2))
    print(json.dumps({'html': str(output), 'manifest': str(manifest_path), 'slides': len(sections)}, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--deck', required=True, help='JSON с содержанием слайдов')
    parser.add_argument('--output', required=True, help='Путь итогового .html')
    args = parser.parse_args()
    try:
        build(args.deck, args.output)
    except (ValueError, KeyError, OSError) as error:
        parser.exit(1, f'Ошибка сборки: {error}\n')
