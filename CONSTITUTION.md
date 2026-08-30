# Shorts Clipper Engineering Constitution

## Build an Evidence-Driven Human Attention Learning System

### Mission

You are not building a Shorts clipping application.

You are building an evidence-driven system that learns what captures and sustains human attention while maximizing long-term viewer satisfaction.

The product is not the rendered video.

The product is a continuously improving understanding of why people decide to keep watching.

Views, subscribers, watch time and virality are outputs.

Understanding human attention is the objective.

---

# Core Philosophy

Engineering exists to reduce uncertainty.

Every feature, model, heuristic, score, and architectural decision must answer one question:

> **What uncertainty does this remove?**

If a feature cannot measurably reduce uncertainty, improve prediction, or improve real-world outcomes, it should not exist.

Never build complexity because it is interesting.

Build complexity only when evidence demonstrates that simpler systems are no longer sufficient.

Assume every assumption is temporary.

Assume every heuristic will eventually be replaced by evidence.

---

# Guiding Principles

## Human First

Optimize for genuine viewer satisfaction.

Never optimize for platform loopholes.

Never optimize for algorithm exploits.

Never optimize for clickbait that damages long-term trust.

The ideal viewer should finish the Short and feel their time was well spent.

---

## Reality Over Theory

Every prediction is a hypothesis.

Every upload is an experiment.

Every experiment produces evidence.

Every future decision must use that evidence.

Until a prediction is validated against real analytics, it remains an opinion.

---

## Explainability

Every decision must be explainable.

Every score must have a reason.

Every feature must have a measurable definition.

Every prediction must be traceable.

If the system cannot explain why it selected a clip, the decision is incomplete.

---

## Modularity

Every subsystem should have one responsibility.

Feature extraction.

Prediction.

Decision policy.

Rendering.

Publishing.

Analytics.

Learning.

Never mix responsibilities simply because they currently work together.

---

## Pragmatic Engineering

Prefer the smallest implementation capable of answering the current question.

Avoid premature machine learning.

Avoid premature deep learning.

Avoid building infrastructure that current data cannot justify.

Prefer deterministic systems until evidence requires probabilistic ones.

---

# Engineering Law

The system must never become more complex faster than it becomes more measurable.

Every increase in sophistication must increase one of the following:

• Explainability

• Predictive accuracy

• Learning capability

• Engineering simplicity

• Viewer satisfaction

Otherwise, the additional complexity is technical debt.

---

# Development Order

Development always follows this order.

Observe

↓

Measure

↓

Understand

↓

Predict

↓

Validate

↓

Learn

↓

Improve

Never reverse this order.

Never skip validation.

Never build prediction before measurement.

Never build learning before collecting evidence.

---

# Phase 0 — Audit

Before writing new systems:

Map the complete pipeline.

Understand every existing subsystem.

Identify what already works.

Identify what is measurable.

Identify what is currently unknown.

Improve existing systems before replacing them.

---

# Phase 1 — Unified Feature Extraction

Extract measurable features.

Nothing inferred.

Nothing imagined.

Only measurable observations.

Collect multimodal information.

Transcript

Audio

Visual

Temporal

Structural

Every feature must include:

• Name

• Definition

• Measurement method

• Computational source

• Expected hypothesis

Examples include:

Transcript:

* speech rate
* filler ratio
* hook timing
* curiosity markers
* question density

Audio:

* loudness
* silence
* pitch variation
* speech energy
* music

Visual:

* face visibility
* face size
* motion
* scene changes
* brightness
* composition
* subtitle timing

Structural:

* clip duration
* hook position
* subtitle delay
* pacing

No feature enters the system without a measurable implementation.

---

# Phase 2 — Explainable Prediction

Convert features into predicted outcomes.

Predict only metrics that can later be validated.

Examples:

Predicted Average Percentage Viewed

Predicted Completion Rate

Predicted Share Probability

Predicted Comment Probability

Predicted Rewatch Probability

Each prediction must include:

Prediction

Confidence

Explanation

Supporting features

No hidden weights.

No magic scores.

No black boxes.

---

# Phase 3 — Decision Policy

Prediction does not decide.

Decision Policy decides.

Prediction estimates outcomes.

Decision Policy chooses clips according to project objectives.

This separation allows future objectives to change without rebuilding prediction systems.

Educational channels may optimize differently from entertainment channels.

The predictors remain identical.

Only policy changes.

---

# Phase 4 — Ground Truth Collection

Publishing is not the end.

Publishing begins learning.

Every upload records:

Extracted features

Predictions

Prediction confidence

Final decision

Rendered clip metadata

YouTube Analytics

Views

Average View Duration

Average Percentage Viewed

Viewed vs Swiped Away

Completion

Retention

Likes

Comments

Shares

Subscribers

Traffic Source

Normalize metrics where appropriate.

Avoid learning directly from raw view counts.

The dataset produced here becomes the project's single source of truth.

---

# Phase 5 — Failure Analysis

Every upload compares:

Prediction

↓

Reality

↓

Prediction Error

Generate an automatic failure report.

Explain:

What was predicted.

What happened.

Largest prediction errors.

Possible contributing features.

Unknown factors.

Every failure must reduce uncertainty.

Failure without learning is wasted data.

---

# Phase 6 — Evidence Review

After a statistically meaningful sample size:

Evaluate every feature independently.

Questions:

Did this feature correlate with success?

Did it improve prediction?

Did it improve ranking?

Did it reduce uncertainty?

If not:

Remove it.

Refactor it.

Or prove why it should remain.

No feature earns permanent existence.

Every feature must continuously justify itself.

---

# Phase 7 — Learned Models

Only after evidence exists.

Replace manually chosen weights with learned weights.

Start simple.

Linear regression.

Logistic regression.

Gradient boosted trees.

Retrain periodically.

Never continuously.

Every learned model must outperform the deterministic baseline before replacing it.

---

# Phase 8 — Advanced Research

Only after the feedback loop is functioning.

Possible future work:

Human attention models

Novelty estimation

Trust estimation

Story momentum

Multimodal embeddings

Reinforcement learning

Counterfactual editing

Candidate generation models

Every advanced system must demonstrate measurable benefit over simpler alternatives.

---

# Continuous Learning Loop

The pipeline never ends.

Discover

↓

Extract Features

↓

Predict

↓

Rank

↓

Render

↓

Publish

↓

Collect Analytics

↓

Compare Prediction vs Reality

↓

Reduce Uncertainty

↓

Improve System

↓

Repeat Forever

Every upload is another experiment.

Every experiment makes the next upload slightly better.

---

# Experiment Registry

Every engineering change must include:

Date

Question

Hypothesis

Implementation

Expected Outcome

Measurement Method

Required Sample Size

Observed Result

Prediction Error

Verdict

Next Action

No experiment is complete until a verdict exists.

---

# System Self-Critique

The system should continuously ask:

What assumptions am I making?

Can they be measured?

Can they be falsified?

Can they be automated?

Should this heuristic still exist?

Is this complexity justified?

What uncertainty remains?

What experiment should reduce it next?

---

# Definition of Success

Version 1 is not a self-improving intelligence.

Version 1 is complete when it can:

Explain every prediction.

Collect real-world outcomes.

Measure prediction accuracy.

Identify why predictions were wrong.

Improve future predictions using evidence instead of intuition.

Long-term success is achieved when every published Short teaches the system something useful about human attention.

The objective is not to build the smartest editor.

The objective is to build the fastest learning system.
