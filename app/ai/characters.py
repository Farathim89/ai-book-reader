"""Who says what: a local LLM (Ollama) labels every dialogue quote with its speaker.

Runs chapter by chapter and saves after each chunk, so it can be stopped and resumed."""
from __future__ import annotations

import re
from typing import Callable

from ..formats.model import Book
from .ollama import Ollama, OllamaError

SYSTEM = """You identify who speaks each line of dialogue in a novel.
Each quote in the passage is preceded by a marker like [Q12].
Rules:
- For every marked quote, give the speaker's name. Use the fullest name the book uses for that person
  (e.g. "Elizabeth Bennet", not "Lizzy" or "she"). If the person is in KNOWN CHARACTERS, use that exact name.
- Use the narration around the quote ("said Anna", "he replied") and the turn-taking of the conversation.
- Use "Unknown" when the speaker cannot be worked out, and "None" when the quoted text is not speech
  (a title, a sign, a single quoted word).
- In "characters" list every person who speaks in this passage with gender "male", "female" or "unknown"
  and any other names/nicknames used for them in the passage.
Answer with JSON only."""

SCHEMA = {
    "type": "object",
    "properties": {
        "characters": {"type": "array", "items": {"type": "object", "properties": {
            "name": {"type": "string"},
            "gender": {"type": "string", "enum": ["male", "female", "unknown"]},
            "aliases": {"type": "array", "items": {"type": "string"}}},
            "required": ["name", "gender"]}},
        "quotes": {"type": "array", "items": {"type": "object", "properties": {
            "id": {"type": "integer"}, "speaker": {"type": "string"}},
            "required": ["id", "speaker"]}},
    },
    "required": ["characters", "quotes"],
}

NO_SPEAKER = {"unknown", "none", "narrator", "", "n/a", "nobody", "unnamed"}


def _norm(name: str) -> str:
    return re.sub(r"\s+", " ", name.strip().strip(".,\"'")).lower()


class CastBook:
    """Character registry with alias resolution."""

    def __init__(self, data: dict):
        self.data = data
        self.chars: dict[str, dict] = data.setdefault("characters", {})
        self.speakers: dict[str, str] = data.setdefault("speakers", {})
        self._index: dict[str, str] = {}
        for name, info in self.chars.items():
            self._index[_norm(name)] = name
            for a in info.get("aliases", []):
                self._index.setdefault(_norm(a), name)

    def resolve(self, name: str) -> str | None:
        n = _norm(name)
        if n in NO_SPEAKER:
            return None
        if n in self._index:
            return self._index[n]
        # "Mr. Darcy" vs "Darcy", "Anna" vs "Anna Smith"
        for key, canon in self._index.items():
            if len(n) > 2 and (n in key.split() or key in n.split()):
                return canon
        return None

    def add(self, name: str, gender: str, aliases: list[str]) -> str | None:
        if _norm(name) in NO_SPEAKER:
            return None
        canon = self.resolve(name)
        if canon is None:
            for a in aliases:
                canon = self.resolve(a)
                if canon:
                    break
        if canon is None:
            canon = name.strip()
            self.chars[canon] = {"gender": gender or "unknown", "aliases": [], "lines": 0}
            self._index[_norm(canon)] = canon
        info = self.chars[canon]
        if info.get("gender", "unknown") == "unknown" and gender in ("male", "female"):
            info["gender"] = gender
        for a in [name, *aliases]:
            if _norm(a) not in NO_SPEAKER and _norm(a) != _norm(canon):
                if a not in info["aliases"]:
                    info["aliases"].append(a)
                self._index.setdefault(_norm(a), canon)
        return canon

    def known_list(self, limit: int = 40) -> str:
        items = sorted(self.chars.items(), key=lambda kv: -kv[1].get("lines", 0))[:limit]
        if not items:
            return "(none yet)"
        out = []
        for name, info in items:
            al = ", ".join(info.get("aliases", [])[:4])
            out.append(f"- {name} ({info.get('gender', 'unknown')}{'; also: ' + al if al else ''})")
        return "\n".join(out)


MALE_TITLES = re.compile(r"\b(mr|sir|lord|king|prince|uncle|father|brother|duke|baron|count|master|mister|"
                         r"grandpa|grandfather|dad|papa|monsieur|herr|señor|signor|lad|boy|man)\b\.?", re.I)
FEMALE_TITLES = re.compile(r"\b(mrs|miss|ms|madam|madame|lady|queen|princess|aunt|auntie|mother|sister|duchess|"
                           r"baroness|countess|mistress|grandma|grandmother|mom|mum|mama|girl|woman|"
                           r"mademoiselle|frau|señora|signora)\b\.?", re.I)
HE = re.compile(r"\b(he|him|his|himself)\b", re.I)
SHE = re.compile(r"\b(she|her|hers|herself)\b", re.I)

GENDER_SCHEMA = {"type": "object", "properties": {"characters": {"type": "array", "items": {
    "type": "object", "properties": {"name": {"type": "string"},
                                     "gender": {"type": "string", "enum": ["male", "female", "unknown"]}},
    "required": ["name", "gender"]}}}, "required": ["characters"]}


def infer_genders(book: Book, data: dict, ollama: Ollama | None = None, model: str | None = None,
                  title: str = "", author: str = "") -> None:
    """Fill in 'unknown' genders: title words in the name, he/she around the character's lines,
    then (optionally) one LLM call that can use what it knows about the book."""
    chars = data.get("characters", {})
    speakers = {int(k): v for k, v in data.get("speakers", {}).items()}
    # 1) pronouns in the narration of paragraphs where only this character speaks
    votes: dict[str, list[int]] = {}
    for ch in book.chapters:
        for p in ch.paragraphs:
            names = {speakers.get(q.qid) for q in p.quotes} - {None}
            if len(names) != 1:
                continue
            name = names.pop()
            narration, pos = [], 0
            for q in p.quotes:
                narration.append(p.text[pos:q.start])
                pos = q.end
            narration.append(p.text[pos:])
            text = " ".join(narration)
            if len(text) > 300:
                continue
            v = votes.setdefault(name, [0, 0])
            v[0] += len(HE.findall(text))
            v[1] += len(SHE.findall(text))
    for name, info in chars.items():
        if info.get("gender") in ("male", "female"):
            continue
        label = " ".join([name, *info.get("aliases", [])])
        m, f = bool(MALE_TITLES.search(label)), bool(FEMALE_TITLES.search(label))
        if m != f:
            info["gender"] = "male" if m else "female"
            continue
        he, she = votes.get(name, [0, 0])
        if he + she >= 3 and max(he, she) / (he + she) >= 0.7:
            info["gender"] = "male" if he > she else "female"
    # 2) ask the model about whoever is still unknown
    unknown = [n for n, i in chars.items() if i.get("gender") not in ("male", "female")]
    if ollama and model and unknown:
        listing = "\n".join(f"- {n}" + (f" (also: {', '.join(chars[n].get('aliases', [])[:3])})"
                                         if chars[n].get("aliases") else "") for n in unknown[:80])
        try:
            res = ollama.chat_json(model, "You know literature well. Answer with JSON only.",
                                   f"Book: {title or book.title} by {author or book.author}.\n"
                                   f"Give the gender of each of these characters from the book:\n{listing}\n"
                                   'Use "unknown" only if you really cannot tell.',
                                   schema=GENDER_SCHEMA, num_ctx=4096)
            for c in res.get("characters", []) or []:
                n, g = str(c.get("name", "")), str(c.get("gender", ""))
                if n in chars and g in ("male", "female") and chars[n].get("gender") not in ("male", "female"):
                    chars[n]["gender"] = g
        except OllamaError:
            pass


def _chunks(book: Book, ci: int, max_words: int = 1400, max_quotes: int = 45):
    """Yield (context_paragraph_texts, marked_paragraph_texts, quote_ids) for chapter ci."""
    paras = book.chapters[ci].paragraphs
    cur: list[str] = []
    qids: list[int] = []
    words = 0
    start = 0
    for i, p in enumerate(paras):
        text = p.text
        if p.quotes:
            pieces, pos = [], 0
            for q in p.quotes:
                pieces.append(text[pos:q.start])
                pieces.append(f"[Q{q.qid}]")
                pieces.append(text[q.start:q.end])
                pos = q.end
                qids.append(q.qid)
            pieces.append(text[pos:])
            text = "".join(pieces)
        cur.append(text)
        words += p.text.count(" ") + 1
        if words >= max_words or len(qids) >= max_quotes:
            if qids:
                yield [x.text for x in paras[max(0, start - 2):start]], cur, qids
            cur, qids, words, start = [], [], 0, i + 1
    if qids:
        yield [x.text for x in paras[max(0, start - 2):start]], cur, qids


def analyze_book(book: Book, data: dict, ollama: Ollama, model: str,
                 progress: Callable[[float, str], None] | None = None,
                 cancelled: Callable[[], bool] | None = None,
                 save: Callable[[dict], None] | None = None,
                 start_chapter: int = 0) -> dict:
    """Fill ``data`` (characters / speakers / done_chapters) for the whole book."""
    progress = progress or (lambda f, m: None)
    cancelled = cancelled or (lambda: False)
    cast = CastBook(data)
    done = set(data.setdefault("done_chapters", []))
    order = list(range(start_chapter, len(book.chapters))) + list(range(0, start_chapter))
    todo = [ci for ci in order if ci not in done and any(p.quotes for p in book.chapters[ci].paragraphs)]
    total_q = sum(len(p.quotes) for ci in todo for p in book.chapters[ci].paragraphs) or 1
    done_q = 0
    if not todo:
        progress(1.0, "Done")
    for ci in todo:
        for context, marked, qids in _chunks(book, ci):
            if cancelled():
                return data
            user = (f"KNOWN CHARACTERS:\n{cast.known_list()}\n\n"
                    + (f"EARLIER TEXT (context only):\n" + "\n\n".join(context) + "\n\n" if context else "")
                    + "PASSAGE:\n" + "\n\n".join(marked)
                    + f"\n\nGive the speaker for each of these quotes: {', '.join(f'Q{q}' for q in qids)}.")
            try:
                res = ollama.chat_json(model, SYSTEM, user, schema=SCHEMA, num_ctx=8192)
            except OllamaError:
                raise
            except Exception as e:
                raise OllamaError(str(e)) from e
            for c in res.get("characters", []) or []:
                if isinstance(c, dict) and c.get("name"):
                    cast.add(str(c["name"]), str(c.get("gender", "unknown")), [str(a) for a in c.get("aliases", []) or []])
            wanted = set(qids)
            for q in res.get("quotes", []) or []:
                if not isinstance(q, dict):
                    continue
                try:
                    qid = int(str(q.get("id", "")).lstrip("Qq"))
                except ValueError:
                    continue
                if qid not in wanted:
                    continue
                name = str(q.get("speaker", ""))
                canon = cast.resolve(name) or cast.add(name, "unknown", [])
                if canon:
                    cast.speakers[str(qid)] = canon
                    cast.chars[canon]["lines"] = cast.chars[canon].get("lines", 0) + 1
                else:
                    cast.speakers.pop(str(qid), None)
            done_q += len(qids)
            progress(min(0.99, done_q / total_q),
                     f"Chapter {ci + 1}/{len(book.chapters)} · {len(cast.chars)} characters found")
            if save:
                save(data)
        done.add(ci)
        data["done_chapters"] = sorted(done)
        if save:
            save(data)
    # drop characters that never actually spoke
    for name in [n for n, i in cast.chars.items() if i.get("lines", 0) == 0]:
        del cast.chars[name]
    progress(0.995, "Working out genders")
    infer_genders(book, data, ollama, model)
    if save:
        save(data)
    progress(1.0, f"{len(cast.chars)} characters")
    return data
