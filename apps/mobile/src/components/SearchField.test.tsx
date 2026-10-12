/// <reference types="jest" />
import AsyncStorage from "@react-native-async-storage/async-storage";
import type { ReactElement } from "react";
import { TextInput } from "react-native";
import { act, create } from "react-test-renderer";
import { SearchField } from "./SearchField";

// react-test-renderer exportiert seinen Rueckgabetyp nicht zuverlaessig als
// benannten Typ (je nach aufgeloester @types-Version). ReturnType<typeof
// create> ist deshalb robuster als ein direkter Typ-Import.
type Renderer = ReturnType<typeof create>;

// @expo/vector-icons laedt seine Schriftart asynchron in componentDidMount
// (Font.loadAsync) und ruft danach setState auf. Ohne await act(async ...)
// meldet React das als "state update not wrapped in act" - render() deshalb
// hier zentral ueber eine kleine async-Hilfsfunktion.
async function renderSearchField(element: ReactElement): Promise<Renderer> {
  let tree!: Renderer;
  await act(async () => {
    tree = create(element);
  });
  return tree;
}

function findInput(tree: Renderer) {
  return tree.root.findByType(TextInput);
}

function findClearButton(tree: Renderer) {
  return tree.root.findByProps({ accessibilityLabel: "Suchtext löschen" });
}

const DEBOUNCE_TEST_MS = 300;

describe("SearchField", () => {
  beforeEach(() => {
    jest.useFakeTimers();
  });

  afterEach(() => {
    jest.useRealTimers();
    jest.clearAllMocks();
  });

  it("aktualisiert das sichtbare Feld sofort, meldet aber erst nach 300ms", async () => {
    const onChangeText = jest.fn();
    const tree = await renderSearchField(<SearchField value="" onChangeText={onChangeText} />);

    act(() => {
      findInput(tree).props.onChangeText("Ede");
    });
    expect(findInput(tree).props.value).toBe("Ede");
    expect(onChangeText).not.toHaveBeenCalled();

    act(() => {
      jest.advanceTimersByTime(DEBOUNCE_TEST_MS - 1);
    });
    expect(onChangeText).not.toHaveBeenCalled();

    act(() => {
      jest.advanceTimersByTime(1);
    });
    expect(onChangeText).toHaveBeenCalledTimes(1);
    expect(onChangeText).toHaveBeenCalledWith("Ede");
  });

  it("fasst schnelle Tastatureingaben innerhalb von 300ms zu genau einer Meldung zusammen", async () => {
    const onChangeText = jest.fn();
    const tree = await renderSearchField(<SearchField value="" onChangeText={onChangeText} />);

    act(() => {
      findInput(tree).props.onChangeText("R");
    });
    act(() => {
      jest.advanceTimersByTime(150);
      findInput(tree).props.onChangeText("Re");
    });
    act(() => {
      jest.advanceTimersByTime(150);
      findInput(tree).props.onChangeText("Rewe");
    });
    act(() => {
      jest.advanceTimersByTime(DEBOUNCE_TEST_MS);
    });

    expect(onChangeText).toHaveBeenCalledTimes(1);
    expect(onChangeText).toHaveBeenCalledWith("Rewe");
  });

  it("zeigt den Loeschen-Button nur bei vorhandenem Text", async () => {
    const tree = await renderSearchField(<SearchField value="" onChangeText={jest.fn()} />);
    expect(() => findClearButton(tree)).toThrow();

    act(() => {
      findInput(tree).props.onChangeText("Aldi");
    });
    expect(findClearButton(tree)).toBeTruthy();
  });

  it("loescht sofort und ohne die 300ms-Verzoegerung abzuwarten", async () => {
    const onChangeText = jest.fn();
    const tree = await renderSearchField(<SearchField value="" onChangeText={onChangeText} />);

    act(() => {
      findInput(tree).props.onChangeText("Aldi");
    });
    onChangeText.mockClear();

    act(() => {
      findClearButton(tree).props.onPress();
    });

    expect(onChangeText).toHaveBeenCalledTimes(1);
    expect(onChangeText).toHaveBeenCalledWith("");
    expect(findInput(tree).props.value).toBe("");
    expect(() => findClearButton(tree)).toThrow();
  });

  it("uebernimmt eine externe Ruecksetzung von aussen, ohne dies erneut zu melden", async () => {
    const onChangeText = jest.fn();
    const tree = await renderSearchField(<SearchField value="Aldi" onChangeText={onChangeText} />);
    expect(findInput(tree).props.value).toBe("Aldi");

    act(() => {
      tree.update(<SearchField value="" onChangeText={onChangeText} />);
    });

    expect(findInput(tree).props.value).toBe("");
    expect(onChangeText).not.toHaveBeenCalled();
  });

  it("schreibt den Suchtext nie in AsyncStorage - Suchbegriffe sind fluechtig", async () => {
    const onChangeText = jest.fn();
    const tree = await renderSearchField(<SearchField value="" onChangeText={onChangeText} />);

    act(() => {
      findInput(tree).props.onChangeText("Rewe");
      jest.advanceTimersByTime(DEBOUNCE_TEST_MS);
    });

    expect((AsyncStorage.setItem as jest.Mock).mock.calls).toHaveLength(0);
  });
});
