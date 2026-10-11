import re
from pathlib import Path
from zipfile import ZipFile

import nltk
from nltk.corpus import wordnet

LEXNAMES = {
    "noun.animal": "Животные",
    "noun.plant": "Природа и погода",
    "noun.body": "Тело и здоровье",
    "noun.food": "Еда и напитки",
    "noun.person": "Люди и профессии",
    "noun.group": "Город и общество",
    "noun.location": "Места и география",
    "noun.time": "Время и числа",
    "noun.quantity": "Время и числа",
    "noun.communication": "Язык и общение",
    "noun.cognition": "Мысли и знания",
    "noun.feeling": "Чувства и характер",
    "noun.possession": "Работа и деньги",
    "noun.event": "События и действия",
    "noun.act": "События и действия",
    "noun.artifact": "Предметы и инструменты",
    "noun.object": "Природа и погода",
    "noun.phenomenon": "Природа и погода",
    "noun.substance": "Материалы и вещества",
    "noun.process": "Процессы и изменения",
    "noun.state": "Состояния",
    "noun.attribute": "Свойства и качества",
    "noun.shape": "Цвета и пространство",
    "noun.relation": "Связи и отношения",
    "noun.motive": "Чувства и характер",
    "verb.motion": "Путешествия и транспорт",
    "verb.communication": "Язык и общение",
    "verb.cognition": "Мысли и знания",
    "verb.emotion": "Чувства и характер",
    "verb.consumption": "Еда и напитки",
    "verb.possession": "Работа и деньги",
    "verb.body": "Тело и здоровье",
    "verb.social": "Семья и отношения",
    "verb.perception": "Восприятие",
    "verb.change": "Процессы и изменения",
    "verb.creation": "Творчество",
    "verb.contact": "События и действия",
    "verb.competition": "Спорт и досуг",
    "verb.stative": "Состояния",
    "verb.weather": "Природа и погода",
    "adj.all": "Свойства и качества",
    "adj.pert": "Свойства и качества",
    "adj.ppl": "Свойства и качества",
    "adv.all": "Наречия и служебные слова",
}


def initialize(cache: Path, licenses: Path):
    corpora = cache / "nltk_data/corpora"
    corpora.mkdir(parents=True, exist_ok=True)
    archive = corpora / "wordnet.zip"
    archive.write_bytes((cache / "wordnet.txt").read_bytes())
    nltk.data.path.insert(0, str(cache / "nltk_data"))
    with ZipFile(archive) as z:
        (licenses / "wordnet.txt").write_bytes(z.read("wordnet/LICENSE"))


def classify(gloss, pos):
    wn_pos = {"n": "n", "v": "v", "adj": "a", "adv": "r"}.get(pos)
    if not wn_pos:
        return ["Наречия и служебные слова"], "part-of-speech", None
    translation = re.sub(r"\([^)]*\)", "", gloss)
    translation = re.split(r"[,;:#]", translation)[0].strip().strip('"')
    translation = re.sub(r"^(to|a|an|the) ", "", translation).strip()
    synsets = wordnet.synsets(translation.replace(" ", "_"), pos=wn_pos)
    if synsets:
        # English translation is only an automatic proxy for the Spanish sense.
        synset = synsets[0]
        topic = LEXNAMES.get(synset.lexname())
        if topic:
            return [topic], "wordnet-first-translation", synset.name()
    fallback = {
        "v": "События и действия",
        "adj": "Свойства и качества",
        "adv": "Наречия и служебные слова",
    }.get(pos, "Общая лексика")
    return [fallback], "part-of-speech" if pos != "n" else "fallback", None
