"""Reproducible licensed dictionary build. No LLM, scraping or invented conjugations."""

import argparse
import concurrent.futures
import csv
import hashlib
import json
import re
import urllib.request
from pathlib import Path

from semantic_topics import classify, initialize

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "data/cache"
SOURCES = {
    "dictionary": ("doozan/spanish_data", "es-en.data"),
    "sentences": ("doozan/spanish_data", "sentences.tsv"),
    "frequency": ("doozan/spanish_data", "frequency.csv"),
    "ipa": ("open-dict-data/ipa-dict", "data/es_ES.txt"),
    "verbs": ("ghidinelli/fred-jehle-spanish-verbs", "jehle_verb_database.csv"),
    "ipa_license": ("open-dict-data/ipa-dict", "LICENSE"),
    "verbs_license": ("ghidinelli/fred-jehle-spanish-verbs", "license.txt"),
    "wordnet": ("nltk/nltk_data", "packages/corpora/wordnet.zip"),
}
TOPICS = {
    "Еда и напитки": "comida alimento pan arroz carne pescado pollo huevo leche queso fruta manzana naranja pera plátano patata tomate cebolla ajo sal azúcar café té agua vino cerveza zumo desayuno almuerzo cena comer beber cocinar cocina restaurante hambre sed plato sopa ensalada mantequilla aceite limón fresa dulce chocolate",
    "Дом и быт": "casa hogar habitación dormitorio cama mesa silla puerta ventana pared techo suelo baño ducha jabón cocina jardín llave lámpara luz sofá armario nevera lavar limpiar toalla almohada manta escalera edificio apartamento piso cuchillo cuchara tenedor vaso taza botella",
    "Семья и отношения": "familia madre padre hijo hija hermano hermana abuelo abuela tío tía primo prima esposo esposa marido mujer novio novia pareja bebé niño niña amigo amiga amistad amor amar querer besar abrazo boda matrimonio vecino",
    "Путешествия и транспорт": "viaje viajar aeropuerto avión tren autobús coche taxi bicicleta barco hotel billete pasaporte maleta equipaje turista turismo estación metro carretera calle mapa playa vacaciones reserva reservar conductor conducir vuelo puerto destino salir llegar caminar andar camino cruzar conducir puente",
    "Тело и здоровье": "cuerpo cabeza cara ojo nariz boca oreja mano brazo pierna pie dedo corazón sangre piel pelo diente espalda estómago salud médico medicina hospital farmacia dolor enfermo enfermedad fiebre respirar dormir despertar cansado sano curar cirugía vacuna músculo hueso cerebro",
    "Работа и деньги": "trabajo trabajar oficina jefe empleado empresa negocio dinero precio pagar comprar vender tienda mercado banco tarjeta factura sueldo salario profesión reunión proyecto contrato cliente venta compra oferta caro barato desempleo abogado contable ingeniero deuda préstamo ahorrar economía moneda efectivo euro dólar",
    "Учёба и язык": "escuela universidad profesor estudiante alumno clase curso libro cuaderno lápiz bolígrafo leer escribir estudiar aprender enseñar idioma lengua palabra frase pregunta respuesta examen letra educación biblioteca diccionario español inglés ruso pronunciar entender traducir gramática historia ciencia conocimiento lección tarea",
    "Природа и погода": "naturaleza árbol flor bosque montaña río mar lago tierra cielo sol luna estrella lluvia nieve viento nube calor frío tiempo clima verano invierno primavera otoño animal perro gato caballo pájaro pez vaca oveja cerdo león ratón insecto arena piedra fuego campo planta hoja rama océano valle isla costa lobo oso lluvia tormenta",
    "Время и числа": "hoy mañana ayer ahora antes después siempre nunca temprano tarde noche día semana mes año hora minuto segundo lunes martes miércoles jueves viernes sábado domingo enero febrero marzo abril mayo junio julio agosto septiembre octubre noviembre diciembre uno dos tres cuatro cinco seis siete ocho nueve diez cien mil primero último pasado futuro presente fecha momento edad",
    "Чувства и характер": "feliz triste alegría tristeza miedo pensar sentir emoción enfadado contento tranquilo nervioso divertido aburrido bueno malo amable valiente tímido inteligente tonto serio fuerte débil fácil difícil sorpresa sorprender esperanza esperar soñar recordar olvidar preocupado preocuparse sonrisa reír llorar felicidad paciencia orgulloso vergüenza odio odiar confianza confiar",
    "Одежда и внешность": "ropa camisa camiseta pantalón falda vestido zapato bota chaqueta abrigo sombrero calcetín gafas guante cinturón bolso bolsillo moda vestir llevar rubio moreno alto bajo hermoso bonito feo delgado gordo elegante limpio sucio peinar espejo",
    "Город и общество": "ciudad pueblo país nación gobierno presidente política ley policía sociedad gente persona hombre mujer ciudadano plaza parque museo iglesia cine teatro calle ayuntamiento cárcel guerra paz justicia derecho libertad seguridad población comunidad público privado votar voto rey reina ejército frontera",
    "Спорт и досуг": "deporte fútbol tenis baloncesto piscina nadar correr saltar jugar juego equipo jugador balón pelota música canción cantar bailar película concierto guitarra piano arte dibujo pintar foto fotografía cámara fiesta ejercicio ganar perder competición paseo ocio descansar afición",
    "Технологии и связь": "teléfono móvil ordenador computadora internet pantalla teclado mensaje llamar llamada correo red programa aplicación archivo contraseña tecnología máquina motor electricidad batería descargar conectar conexión enviar recibir información vídeo radio televisión noticia comunicación imprimir herramienta ciencia robot digital",
    "Цвета и пространство": "color rojo azul verde amarillo blanco negro gris rosa marrón naranja violeta grande pequeño largo corto ancho estrecho cerca lejos dentro fuera arriba abajo izquierda derecha delante detrás norte sur este oeste centro lado lugar distancia dirección tamaño forma redondo cuadrado recto horizontal vertical",
}
# Transparent keyword labels supplement explicit headword lists; they are not a CEFR taxonomy.
KEYWORDS = {
    "Еда и напитки": r"\b(food|fruit|vegetable|beverage|meal|bread|cheese|cooking)\b",
    "Дом и быт": r"\b(furniture|household|bedroom|kitchen|utensil)\b",
    "Природа и погода": r"\b(botany|zoology|weather|mammal|bird|plant|tree|flower|insect)\b",
    "Тело и здоровье": r"\b(anatomy|medical|disease|medicine|body part)\b",
    "Работа и деньги": r"\b(finance|business|banking|employment|currency)\b",
    "Учёба и язык": r"\b(linguistics|grammar|education|language|school)\b",
    "Город и общество": r"\b(politics|government|military|legal|law enforcement)\b",
    "Спорт и досуг": r"\b(sports|music|musical|football|game|artistic)\b",
    "Технологии и связь": r"\b(computing|computer|technology|telecommunications|engineering)\b",
    "Одежда и внешность": r"\b(clothing|garment|fashion|footwear)\b",
}


def request(url):
    return urllib.request.urlopen(
        urllib.request.Request(url, headers={"User-Agent": "vocabulario/0.1"}), timeout=90
    )


def download(name, repo, filename, revision, expected=None):
    target = CACHE / (name + ".txt")
    url = f"https://raw.githubusercontent.com/{repo}/{revision}/{filename}"
    if target.exists() and expected and expected["url"] == url:
        actual = hashlib.sha256(target.read_bytes()).hexdigest()
        if actual != expected["sha256"]:
            raise ValueError(f"Cached source checksum mismatch: {name}. Restore the source before building.")
    if not target.exists():
        temporary = target.with_suffix(".tmp")
        with request(url) as response, temporary.open("wb") as out:
            while chunk := response.read(1024 * 1024):
                out.write(chunk)
        temporary.replace(target)
    return name, {"url": url, "revision": revision, "sha256": hashlib.sha256(target.read_bytes()).hexdigest()}


def build(limit=5000):
    CACHE.mkdir(parents=True, exist_ok=True)
    manifest_path = ROOT / "data/sources.json"
    existing = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    revisions = {}
    for repo in {repo for repo, _ in SOURCES.values()}:
        pinned = next((x["revision"] for x in existing.values() if repo in x["url"]), None)
        if not pinned:
            with request(f"https://api.github.com/repos/{repo}/commits?per_page=1") as r:
                pinned = json.load(r)[0]["sha"]
        revisions[repo] = pinned
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        futures = [
            pool.submit(download, name, repo, filename, revisions[repo], existing.get(name))
            for name, (repo, filename) in SOURCES.items()
        ]
        manifest = dict(f.result() for f in futures)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Sources downloaded", flush=True)
    license_dir = ROOT / "data/licenses"
    license_dir.mkdir(exist_ok=True)
    initialize(CACHE, license_dir)
    frequency = []
    with (CACHE / "frequency.txt").open(encoding="utf-8", newline="") as f:
        for rank, row in enumerate(csv.DictReader(f), 1):
            if row["pos"] in {"n", "v", "adj", "adv", "prep", "conj", "pron", "num", "art", "interj"}:
                frequency.append((rank, row["spanish"], row["pos"]))
    wanted = {(word, pos) for _, word, pos in frequency}
    meanings = {}
    text = (CACHE / "dictionary.txt").read_text(encoding="utf-8")
    for block in text.split("_____\n"):
        lines = block.strip().splitlines()
        if not lines:
            continue
        word = lines[0]
        pos = None
        for line in lines[1:]:
            if line.startswith("pos: "):
                pos = line[5:]
            elif line.startswith("  gloss: ") and (word, pos) in wanted:
                gloss = line[9:]
                if not re.match(
                    r"(alternative|obsolete|misspelling|plural|feminine|pronunciation|inflection|verb form)",
                    gloss,
                    flags=re.IGNORECASE,
                ):
                    meanings.setdefault((word, pos), []).append(gloss)
    ipa = {}
    with (CACHE / "ipa.txt").open(encoding="utf-8") as f:
        for line in f:
            word, _, pronunciation = line.strip().partition("\t")
            ipa[word] = pronunciation
    conjugations = {}
    with (CACHE / "verbs.txt").open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            if not row.get("infinitive"):
                continue
            conjugations.setdefault(row["infinitive"], {})[row["mood"] + " · " + row["tense"]] = {
                pronoun: row[key]
                for pronoun, key in zip(
                    ["yo", "tú", "él/ella/usted", "nosotros", "vosotros", "ellos/ustedes"],
                    ["form_1s", "form_2s", "form_3s", "form_1p", "form_2p", "form_3p"],
                )
                if row[key]
            }
    examples = {}
    with (CACHE / "sentences.txt").open(encoding="utf-8") as f:
        for line in f:
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 6:
                continue
            en, es, attribution, _, proficiency, tags = parts[:6]
            if len(es) > 160 or len(es) < 8 or int(proficiency) < 5:
                continue
            for tagged in tags.split(" :"):
                tagged = tagged.lstrip(":")
                pos, _, forms = tagged.partition(",")
                for form in forms.split(","):
                    lemma = form.split("|")[-1]
                    key = (lemma, pos)
                    if key not in wanted:
                        continue
                    bucket = examples.setdefault(key, [])
                    if len(bucket) < 3 and all(x["es"] != es for x in bucket):
                        ids = re.findall(r"#(\d+)", attribution)
                        bucket.append(
                            {
                                "es": es,
                                "en": en,
                                "attribution": attribution,
                                "source": f"https://tatoeba.org/en/sentences/show/{ids[-1]}"
                                if ids
                                else "https://tatoeba.org",
                            }
                        )
    records = []
    explicit = {topic: set(words.split()) for topic, words in TOPICS.items()}
    for rank, word, pos in frequency:
        key = (word, pos)
        if not meanings.get(key) or not ipa.get(word) or len(examples.get(key, [])) < 2:
            continue
        if pos == "v" and word not in conjugations:
            continue
        glosses = list(dict.fromkeys(meanings[key]))[:3]
        topics = [topic for topic, words in explicit.items() if word in words]
        method, synset = "headword-list", None
        if not topics:
            method = "gloss-keywords"
            topics = [
                topic
                for topic, pattern in KEYWORDS.items()
                if re.search(pattern, " ".join(glosses), flags=re.IGNORECASE)
            ]
        if not topics:
            topics, method, synset = classify(glosses[0], pos)
        if pos == "v":
            topics.append("Глаголы")
        records.append(
            {
                "id": hashlib.sha256(f"{word}:{pos}".encode()).hexdigest()[:12],
                "word": word,
                "pos": pos,
                "rank": rank,
                "meanings": glosses,
                "ipa": ipa[word],
                "ipa_note": "es_ES; experimental, rule-generated IPA",
                "topics": topics,
                "topic_method": method,
                "topic_synset": synset,
                "examples": examples[key],
                "conjugations": conjugations.get(word, {}) if pos == "v" else {},
                "source": "https://en.wiktionary.org/wiki/" + urllib.parse.quote(word) + "#Spanish",
            }
        )
        if len(records) >= limit:
            break
    output = ROOT / "data/dictionary.jsonl"
    output.write_text("".join(json.dumps(w, ensure_ascii=False) + "\n" for w in records), encoding="utf-8")
    license_dir = ROOT / "data/licenses"
    license_dir.mkdir(exist_ok=True)
    for name in ("ipa", "verbs"):
        (license_dir / (name + ".txt")).write_bytes((CACHE / (name + "_license.txt")).read_bytes())
    print(
        json.dumps(
            {
                "words": len(records),
                "verbs": sum(w["pos"] == "v" for w in records),
                "examples": sum(len(w["examples"]) for w in records),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=5000)
    build(parser.parse_args().limit)
