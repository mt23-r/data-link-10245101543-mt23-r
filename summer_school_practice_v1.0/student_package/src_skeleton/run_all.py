import m2_protocol
import m3_tracks
import m4_mapping
import m5_quality


def main() -> None:
    m2_protocol.run_m2()
    m3_tracks.run_m3()
    m4_mapping.map_with_verified_rules()
    m5_quality.run_m5()


if __name__ == "__main__":
    main()
