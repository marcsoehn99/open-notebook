# Langfuse evaluation datasets

A Langfuse dataset is a saved list of examples used to check whether a language-model workflow still answers well. This page describes the example Open Notebook would store. Nothing here is built yet.

## What one example is

One dataset item has two jobs, kept apart.

The input is a question about one or more sources already in a notebook. The question is what a person would ask in Ask or in chat. It names the thing they want from those sources, in the words they would type.

The expected output is the answer a reviewer would accept for that question. That answer is the ground truth for the example. Notes about why it is right belong in metadata, beside the item, if a later version needs them.

A useful example can be graded. Someone who knows the sources can say the expected answer is right or wrong without running the app. A vague question, or an expected answer that only restates the question, does not do that.

## What Quiz me would do with it

Quiz me would be a later feature. It would take a dataset item, ask that question against the linked sources, and compare the model's answer to the expected answer. The dataset is the list of questions and accepted answers. The feature would be the run.

Tracing, described in the [Environment Reference](../5-CONFIGURATION/environment-reference.md#logging-and-tracing), records live model calls. A dataset is a different object. Traces show what the model did. Dataset items say what it should have answered. Quiz me would need both, later. This page does not add a dataset, a score, or a button.
