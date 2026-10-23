try:
    from scripts.create_1click_colab import main
except ModuleNotFoundError:
    from create_1click_colab import main


if __name__ == "__main__":
    main()
