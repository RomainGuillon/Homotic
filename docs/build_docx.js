/* Assemble la documentation Markdown de Homotic en un seul .docx.
   Les fichiers .md restent la source ; ce script ne fait que les rendre. */
const fs = require('fs');
const path = require('path');
const {
  Document, Packer, Paragraph, TextRun, ExternalHyperlink, HeadingLevel,
  AlignmentType, Table, TableRow, TableCell, WidthType, ShadingType,
  BorderStyle, TableOfContents, PageBreak, PageNumber, Header, Footer,
  LevelFormat, convertInchesToTwip, ImageRun,
} = require('docx');

/* Largeur utile d'une page A4 avec les marges de ce document, en pixels à
   96 dpi. Une capture plus large est réduite, jamais agrandie. */
const LARGEUR_UTILE = 620;
const HAUTEUR_MAX = 760;  // au-delà, la capture déborderait de la page

/* Dimensions d'un PNG : elles sont dans l'en-tête IHDR, octets 16 à 24. */
function tailleImage(buffer) {
  if (buffer.length < 24 || buffer.toString('ascii', 12, 16) !== 'IHDR') return null;
  return { largeur: buffer.readUInt32BE(16), hauteur: buffer.readUInt32BE(20) };
}

/* Retrouve une capture référencée « images/x.png » depuis le Markdown, que
   celui-ci soit à la racine de docs/ (français) ou dans en/ ou de/. */
function cheminImage(reference) {
  const base = fs.existsSync(SOURCE) && fs.statSync(SOURCE).isDirectory()
    ? SOURCE : path.dirname(SOURCE);
  for (const candidat of [path.join(base, reference), path.join(base, '..', reference)]) {
    if (fs.existsSync(candidat)) return candidat;
  }
  return null;
}

/* Usage :
     node build_docx.js <docs/>            <sortie.docx> [langue]
     node build_docx.js <un-seul-fichier.md> <sortie.docx> [langue]

   Le premier argument accepte le répertoire des chapitres français (un
   fichier par chapitre) ou un unique Markdown contenant tous les chapitres,
   ce qui est le cas des traductions. */
const SOURCE = process.argv[2];
const SORTIE = process.argv[3];
const LANGUE = process.argv[4] || 'fr';

const FICHIERS = [
  ['01-installation.md', 'Installation'],
  ['02-prise-en-main.md', 'Prise en main'],
  ['03-tableau-de-bord.md', 'Le tableau de bord'],
  ['04-scenarios.md', 'Les scénarios'],
  ['05-modules-livres.md', 'Les modules livrés'],
  ['06-creer-un-module.md', 'Créer un module'],
  ['07-reference-contrats.md', 'Référence des contrats'],
  ['08-depannage.md', 'Dépannage'],
];

/* Libellés de l'habillage (page de titre, sommaire, cadres de capture). */
const TEXTES = {
  fr: {
    locale: 'fr-FR',
    sousTitre: 'Documentation',
    accroche: 'Tableau de bord domestique modulaire : mesures en direct, scénarios '
      + "d'automatisation, et optimisation selon la production solaire et les tarifs EDF.",
    sommaire: 'Sommaire',
    astuceSommaire: "Sommaire vide à l'ouverture ? Ctrl+A puis F9 met les champs à jour "
      + "(Word ne calcule les numéros de page qu'à la demande).",
    entete: 'Homotic — Documentation',
    capture: 'Capture à insérer',
    navigation: /^\[← Sommaire\]/,
  },
  en: {
    locale: 'en-GB',
    sousTitre: 'Documentation',
    accroche: 'Modular home dashboard: live measurements, automation scenarios, and '
      + 'optimisation based on solar production and electricity tariffs.',
    sommaire: 'Contents',
    astuceSommaire: 'Contents empty on opening? Press Ctrl+A then F9 to update the fields '
      + '(Word only computes page numbers on demand).',
    entete: 'Homotic — Documentation',
    capture: 'Screenshot to insert',
    navigation: /^\[← Contents\]/,
  },
  de: {
    locale: 'de-DE',
    sousTitre: 'Dokumentation',
    accroche: 'Modulares Haus-Dashboard: Messwerte in Echtzeit, Automatisierungsszenarien '
      + 'und Optimierung nach Solarertrag und Stromtarifen.',
    sommaire: 'Inhalt',
    astuceSommaire: 'Inhaltsverzeichnis beim Öffnen leer? Strg+A und dann F9 aktualisiert die '
      + 'Felder (Word berechnet Seitenzahlen nur auf Anforderung).',
    entete: 'Homotic — Dokumentation',
    capture: 'Screenshot einfügen',
    navigation: /^\[← Inhalt\]/,
  },
}[LANGUE];

const GRIS = '5F5E5A';
const ACCENT = '1F6F54';
const FOND_CODE = 'F2F1EC';
const FOND_ENTETE = 'E8EDEA';

/* Début d'un nouveau bloc : titre, citation, tableau, code, image, liste.
   Sert à savoir où s'arrête un paragraphe ou un élément de liste. */
const DEBUT_BLOC = /^(#{1,4}\s|>|\||```|!\[|\s*[-*]\s|\s*\d+\.\s)/;

/* ---------- formatage en ligne : **gras**, `code`, [lien](url) ---------- */
function runs(texte, base = {}) {
  // Lien automatique <http://…> : on retire les chevrons
  texte = texte.replace(/<((?:https?|mailto):[^>]+)>/g, '$1');
  const out = [];
  const re = /(\*\*[^*]+\*\*|`[^`]+`|\[[^\]]+\]\([^)]+\))/g;
  let reste = 0;
  let m;
  while ((m = re.exec(texte)) !== null) {
    if (m.index > reste) out.push(new TextRun({ text: texte.slice(reste, m.index), ...base }));
    const jeton = m[0];
    if (jeton.startsWith('**')) {
      out.push(new TextRun({ text: jeton.slice(2, -2), bold: true, ...base }));
    } else if (jeton.startsWith('`')) {
      out.push(new TextRun({
        text: jeton.slice(1, -1), font: 'Consolas', size: 19,
        shading: { type: ShadingType.CLEAR, fill: FOND_CODE }, ...base,
      }));
    } else {
      const sep = jeton.indexOf('](');
      const libelle = jeton.slice(1, sep);
      const cible = jeton.slice(sep + 2, -1);
      if (/^https?:/.test(cible)) {
        out.push(new ExternalHyperlink({
          link: cible,
          children: [new TextRun({ text: libelle, style: 'Hyperlink', ...base })],
        }));
      } else {
        // Lien interne au dépôt : on garde le texte, sans le chemin
        out.push(new TextRun({ text: libelle, italics: true, ...base }));
      }
    }
    reste = m.index + jeton.length;
  }
  if (reste < texte.length) out.push(new TextRun({ text: texte.slice(reste), ...base }));
  return out.length ? out : [new TextRun({ text: '', ...base })];
}

function cellules(ligne) {
  return ligne.trim().replace(/^\|/, '').replace(/\|$/, '').split('|').map((c) => c.trim());
}

/* ---------- conversion d'un fichier Markdown ---------- */
/* Chaque liste numérotée doit repartir de 1 : dans docx, cela demande une
   « instance » distincte de la même définition de numérotation. */
let instanceListe = 0;
let dansListeNumerotee = false;

function convertir(md, decalageTitre) {
  const lignes = md.split('\n');
  const blocs = [];
  let i = 0;

  while (i < lignes.length) {
    const ligne = lignes[i];

    // Lien de navigation en tête de fichier : inutile sur papier
    if (TEXTES.navigation.test(ligne)) { i++; continue; }

    // Bloc de code
    if (ligne.startsWith('```')) {
      i++;
      const code = [];
      while (i < lignes.length && !lignes[i].startsWith('```')) code.push(lignes[i++]);
      i++;
      code.forEach((l, idx) => blocs.push(new Paragraph({
        children: [new TextRun({ text: l || ' ', font: 'Consolas', size: 18 })],
        shading: { type: ShadingType.CLEAR, fill: FOND_CODE },
        spacing: {
          before: idx === 0 ? 120 : 0,
          after: idx === code.length - 1 ? 160 : 0,
          line: 200,  // interligne serré : un bloc de code doit rester compact
        },
        indent: { left: convertInchesToTwip(0.15) },
      })));
      continue;
    }

    // Tableau
    if (ligne.trim().startsWith('|') && (lignes[i + 1] || '').includes('---')) {
      const entete = cellules(ligne);
      i += 2;
      const corps = [];
      while (i < lignes.length && lignes[i].trim().startsWith('|')) corps.push(cellules(lignes[i++]));

      const largeurTotale = 9000;
      const largeurs = entete.map(() => Math.floor(largeurTotale / entete.length));
      largeurs[0] += largeurTotale - largeurs.reduce((a, b) => a + b, 0);

      const rangee = (valeurs, entetes) => new TableRow({
        tableHeader: entetes,
        children: valeurs.map((v, idx) => new TableCell({
          width: { size: largeurs[idx], type: WidthType.DXA },
          shading: entetes ? { type: ShadingType.CLEAR, fill: FOND_ENTETE } : undefined,
          margins: { top: 60, bottom: 60, left: 100, right: 100 },
          children: [new Paragraph({
            children: runs(v, entetes ? { bold: true, size: 19 } : { size: 19 }),
            spacing: { before: 0, after: 0 },
          })],
        })),
      });

      blocs.push(new Table({
        columnWidths: largeurs,
        width: { size: largeurTotale, type: WidthType.DXA },
        rows: [rangee(entete, true), ...corps.map((c) => rangee(c, false))],
      }));
      blocs.push(new Paragraph({ text: '', spacing: { after: 160 } }));
      continue;
    }

    // Image : la capture si elle existe, sinon un cadre réservé
    const img = ligne.match(/^!\[([^\]]*)\]\(([^)]+)\)/);
    if (img) {
      const fichier = cheminImage(img[2]);
      const donnees = fichier ? fs.readFileSync(fichier) : null;
      const taille = donnees ? tailleImage(donnees) : null;

      if (taille) {
        // Mise à l'échelle : on respecte les proportions, et on borne aussi
        // la hauteur pour qu'une capture très verticale tienne sur la page.
        let l = Math.min(LARGEUR_UTILE, taille.largeur);
        let h = Math.round((taille.hauteur * l) / taille.largeur);
        if (h > HAUTEUR_MAX) {
          l = Math.round((l * HAUTEUR_MAX) / h);
          h = HAUTEUR_MAX;
        }
        blocs.push(new Paragraph({
          children: [new ImageRun({ type: 'png', data: donnees, transformation: { width: l, height: h } })],
          alignment: AlignmentType.CENTER,
          spacing: { before: 200, after: 60 },
          keepNext: true,  // la légende ne doit pas partir sur la page suivante
        }));
        if (img[1]) {
          blocs.push(new Paragraph({
            children: [new TextRun({ text: img[1], italics: true, color: GRIS, size: 18 })],
            alignment: AlignmentType.CENTER,
            spacing: { after: 220 },
          }));
        }
      } else {
        blocs.push(new Paragraph({
          children: [new TextRun({
            text: `[${TEXTES.capture} — ${path.basename(img[2])} : ${img[1]}]`,
            italics: true, color: GRIS, size: 19,
          })],
          alignment: AlignmentType.CENTER,
          border: {
            top: { style: BorderStyle.DASHED, size: 4, color: 'C3C2B7' },
            bottom: { style: BorderStyle.DASHED, size: 4, color: 'C3C2B7' },
            left: { style: BorderStyle.DASHED, size: 4, color: 'C3C2B7' },
            right: { style: BorderStyle.DASHED, size: 4, color: 'C3C2B7' },
          },
          spacing: { before: 160, after: 200 },
        }));
      }
      i++;
      continue;
    }

    // Titres
    const titre = ligne.match(/^(#{1,4})\s+(.*)$/);
    if (titre) {
      const niveau = Math.min(titre[1].length + decalageTitre, 5);
      const texte = titre[2].replace(/^\d+\.\s*/, '');
      blocs.push(new Paragraph({
        children: runs(texte),
        heading: [null, HeadingLevel.HEADING_1, HeadingLevel.HEADING_2,
          HeadingLevel.HEADING_3, HeadingLevel.HEADING_4, HeadingLevel.HEADING_5][niveau],
        spacing: { before: niveau <= 2 ? 320 : 240, after: 120 },
        pageBreakBefore: niveau === 1,
      }));
      i++;
      continue;
    }

    // Citation / encadré d'avertissement
    if (ligne.startsWith('> ')) {
      const texte = [];
      while (i < lignes.length && lignes[i].startsWith('>')) texte.push(lignes[i++].replace(/^>\s?/, ''));
      blocs.push(new Paragraph({
        children: runs(texte.join(' ').trim()),
        indent: { left: convertInchesToTwip(0.25) },
        border: { left: { style: BorderStyle.SINGLE, size: 12, color: ACCENT, space: 12 } },
        spacing: { before: 160, after: 200 },
      }));
      continue;
    }

    // Listes
    const puce = ligne.match(/^(\s*)[-*]\s+(.*)$/);
    const numero = ligne.match(/^(\s*)\d+\.\s+(.*)$/);
    if (puce || numero) {
      const m2 = puce || numero;
      const niveau = Math.min(Math.floor(m2[1].length / 2), 2);
      // Un élément de liste peut tenir sur plusieurs lignes dans le Markdown :
      // on recolle la suite, sinon elle devient un paragraphe orphelin.
      const morceaux = [m2[2]];
      i++;
      while (i < lignes.length && lignes[i].trim() && !DEBUT_BLOC.test(lignes[i])) {
        morceaux.push(lignes[i++].trim());
      }
      if (numero && !dansListeNumerotee) instanceListe += 1;  // nouvelle liste
      dansListeNumerotee = Boolean(numero);
      blocs.push(new Paragraph({
        children: runs(morceaux.join(' ')),
        numbering: puce
          ? { reference: 'puces', level: niveau }
          : { reference: 'numeros', level: niveau, instance: instanceListe },
        spacing: { before: 40, after: 40 },
      }));
      continue;
    }
    dansListeNumerotee = false;

    // Ligne vide
    if (!ligne.trim()) { i++; continue; }

    // Paragraphe : on recolle les lignes consécutives
    const para = [];
    while (i < lignes.length && lignes[i].trim() && !DEBUT_BLOC.test(lignes[i])) {
      para.push(lignes[i++].trim());
    }
    if (para.length) {
      blocs.push(new Paragraph({
        children: runs(para.join(' ')),
        spacing: { before: 60, after: 140, line: 280 },
        alignment: AlignmentType.JUSTIFIED,
      }));
    } else { i++; }
  }
  return blocs;
}

/* ---------- assemblage ---------- */
const contenu = [];

// Page de titre
contenu.push(
  new Paragraph({ text: '', spacing: { before: 2600 } }),
  new Paragraph({
    children: [new TextRun({ text: 'Homotic', bold: true, size: 72, color: ACCENT })],
    alignment: AlignmentType.CENTER,
  }),
  new Paragraph({
    children: [new TextRun({ text: TEXTES.sousTitre, size: 40, color: GRIS })],
    alignment: AlignmentType.CENTER,
    spacing: { after: 400 },
  }),
  new Paragraph({
    children: [new TextRun({
      text: TEXTES.accroche,
      size: 22, color: GRIS,
    })],
    alignment: AlignmentType.CENTER,
    indent: { left: convertInchesToTwip(1), right: convertInchesToTwip(1) },
  }),
  new Paragraph({
    children: [new TextRun({
      text: new Date().toLocaleDateString(TEXTES.locale, { day: 'numeric', month: 'long', year: 'numeric' }),
      size: 20, color: GRIS,
    })],
    alignment: AlignmentType.CENTER,
    spacing: { before: 600 },
  }),
  new Paragraph({ children: [new PageBreak()] }),
);

// Sommaire
contenu.push(
  new Paragraph({
    children: [new TextRun({ text: TEXTES.sommaire, bold: true, size: 36 })],
    spacing: { after: 240 },
  }),
  new Paragraph({
    children: [new TextRun({
      text: TEXTES.astuceSommaire,
      italics: true, size: 18, color: GRIS,
    })],
    spacing: { after: 240 },
  }),
  new TableOfContents(TEXTES.sommaire, { hyperlink: true, headingStyleRange: '1-3' }),
  new Paragraph({ children: [new PageBreak()] }),
);

if (fs.statSync(SOURCE).isDirectory()) {
  FICHIERS.forEach(([fichier]) => {
    contenu.push(...convertir(fs.readFileSync(path.join(SOURCE, fichier), 'utf8'), 0));
  });
  const inventaire = fs.readFileSync(path.join(SOURCE, 'images', 'README.md'), 'utf8')
    .replace(/^\[← [^\]]+\][^\n]*\n/, '');
  contenu.push(...convertir(inventaire, 0));
} else {
  // Traduction : un seul Markdown contenant tous les chapitres
  contenu.push(...convertir(fs.readFileSync(SOURCE, 'utf8'), 0));
}

const doc = new Document({
  creator: 'Homotic',
  title: 'Homotic — Documentation',
  description: 'Installation, paramétrage, utilisation et création de modules',
  styles: {
    default: {
      document: { run: { font: 'Calibri', size: 22 } },
      heading1: { run: { font: 'Calibri', size: 36, bold: true, color: ACCENT } },
      heading2: { run: { font: 'Calibri', size: 28, bold: true, color: '2C2C2A' } },
      heading3: { run: { font: 'Calibri', size: 24, bold: true, color: '444441' } },
      heading4: { run: { font: 'Calibri', size: 22, bold: true, color: GRIS } },
    },
  },
  numbering: {
    config: [
      {
        reference: 'puces',
        levels: [0, 1, 2].map((l) => ({
          level: l, format: LevelFormat.BULLET, text: ['•', '–', '·'][l],
          alignment: AlignmentType.LEFT,
          style: { paragraph: { indent: { left: 360 + l * 360, hanging: 260 } } },
        })),
      },
      {
        reference: 'numeros',
        levels: [0, 1, 2].map((l) => ({
          level: l, format: LevelFormat.DECIMAL, text: `%${l + 1}.`,
          alignment: AlignmentType.LEFT,
          style: { paragraph: { indent: { left: 360 + l * 360, hanging: 260 } } },
        })),
      },
    ],
  },
  sections: [{
    properties: { page: { margin: { top: 1100, bottom: 1100, left: 1200, right: 1200 } } },
    headers: {
      default: new Header({
        children: [new Paragraph({
          children: [new TextRun({ text: TEXTES.entete, size: 18, color: GRIS })],
          alignment: AlignmentType.RIGHT,
          border: { bottom: { style: BorderStyle.SINGLE, size: 4, color: 'D3D1C7', space: 6 } },
        })],
      }),
    },
    footers: {
      default: new Footer({
        children: [new Paragraph({
          children: [new TextRun({ children: ['Page ', PageNumber.CURRENT, ' / ', PageNumber.TOTAL_PAGES], size: 18, color: GRIS })],
          alignment: AlignmentType.CENTER,
        })],
      }),
    },
    children: contenu,
  }],
});

Packer.toBuffer(doc).then((buf) => {
  fs.writeFileSync(SORTIE, buf);
  console.log('écrit :', SORTIE, Math.round(buf.length / 1024), 'ko');
});
