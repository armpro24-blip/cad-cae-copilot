import { CredibilityBadge } from "./CredibilityBadge";
import { InfoTip } from "./InfoTip";
import { credibilityTierKey, glossaryText } from "../app/glossary";
import type { ResultsHeroView } from "../app/resultsHero";

type ResultsHeroProps = {
  hero: ResultsHeroView | null;
};

function formatValue(value: number): string {
  if (Math.abs(value) >= 1e6 || (Math.abs(value) < 1e-3 && value !== 0)) {
    return value.toExponential(3);
  }
  return value.toPrecision(4);
}

/**
 * Results summary hero (#400): the product's output stated as a plain-language
 * verdict — headline metric(s), the V&V-40 credibility tier with its meaning,
 * and an honest "what wasn't modeled" line. Read-only; renders nothing until a
 * result exists. Never presents a lower tier as solver-grade (the tier comes
 * straight from resolveResultsHero's honest derivation).
 */
export function ResultsHero({ hero }: ResultsHeroProps) {
  if (!hero) return null;
  const tierKey = credibilityTierKey(hero.credibility.tier);

  return (
    <section className="results-hero" aria-label="Simulation result summary">
      <div className="results-hero-top">
        <div className="results-hero-verdict-wrap">
          <span className={`results-hero-verdict verdict-${hero.verdict.kind}`}>{hero.verdict.text}</span>
          {hero.analysisType ? <span className="results-hero-analysis">{hero.analysisType}</span> : null}
        </div>
        <div className="results-hero-cred">
          <CredibilityBadge credibility={hero.credibility} />
          <InfoTip
            text={tierKey ? glossaryText(tierKey) : hero.credibility.evidence_basis ?? "Trust level of this result."}
            label="What does this credibility tier mean?"
          />
        </div>
      </div>

      {hero.metrics.length > 0 ? (
        <dl className="results-hero-metrics">
          {hero.metrics.map((m) => (
            <div key={m.key} className="results-hero-metric">
              <dt>{m.label}</dt>
              <dd>
                {formatValue(m.value)}
                {m.unit ? <span className="results-hero-unit"> {m.unit}</span> : null}
              </dd>
            </div>
          ))}
        </dl>
      ) : null}

      {hero.oneLine ? <p className="results-hero-oneline">{hero.oneLine}</p> : null}

      {hero.limitations.length > 0 ? (
        <div className="results-hero-notmodeled">
          <span className="results-hero-notmodeled-label">Not modeled</span>
          <ul>
            {hero.limitations.map((lim, i) => (
              <li key={i}>{lim}</li>
            ))}
          </ul>
        </div>
      ) : null}
    </section>
  );
}
